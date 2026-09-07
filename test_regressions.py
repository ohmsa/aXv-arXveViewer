# SPDX-License-Identifier: Apache-2.0
"""Run with python -m unittest -v test_regressions (no GPU/model required)."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import tempfile
import subprocess
import time
import zipfile
import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtCore import QThread
import viewer
import ai_upscale

APP = QApplication.instance() or QApplication([])

class FakeSingle(ai_upscale.AIUpscaleWorker):
    starts = []
    failures = set()
    def run(self):
        self.starts.append(('single', self._generation, [self.request_token[0]]))
        QThread.msleep(4)
        if self._cancel_requested:
            self.cancelled.emit(self.request_token)
        elif self.request_token[0] in self.failures:
            self.failed.emit(self.request_token, 'injected single failure')
        else:
            self.finished.emit(self.request_token, self.image, self.engine_key, self.model_name)

class FakeBatch(ai_upscale.BatchAIUpscaleWorker):
    fail = False
    def run(self):
        FakeSingle.starts.append(('batch', self._generation, [key[0] for key, _ in self.items]))
        QThread.msleep(4)
        if self._cancel_requested:
            self.cancelled.emit()
        elif self.fail:
            self.failed.emit('injected batch failure')
        else:
            for key, image in self.items:
                self.item_finished.emit(key, image)
            self.batch_finished.emit()

def spin_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if predicate():
            return
        time.sleep(.001)
    raise AssertionError('Qt event loop did not reach the expected state')

class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.settings_patch = patch.object(viewer, 'load_settings', return_value=dict(viewer.DEFAULT_SETTINGS))
        self.settings_patch.start()
        self.v = viewer.ImageViewer()
        self.v.ai_upscale_enabled = True
        self.v.zoom_mode = '200'
        self.v._start_sibling_prefetch = Mock()
        self.image = QImage(32, 32, QImage.Format_RGB32)
        self.image.fill(0xff808080)

    def tearDown(self):
        # Even a failed assertion must not destroy a running QThread.
        self.v._closing = True
        self.v._ai_queue.clear()
        for entry in (self.v._ai_thread_ref, self.v._batch_thread_ref):
            if entry is not None:
                entry[1].request_cancel()
        spin_until(lambda: self.v._ai_thread_ref is None and self.v._batch_thread_ref is None
                   and not self.v._active_prefetch_threads)
        self.v.close()
        APP.processEvents()
        self.settings_patch.stop()

    def test_batch_completion_releases_queue(self):
        v = self.v
        thread, worker = Mock(), Mock()
        worker._generation = v._archive_generation
        worker._cancel_requested = False
        v._batch_thread_ref = (thread, worker)
        v._ai_queue = [(self.image, (0, 0, 'original'), 1, v.ai_upscale_model)]
        v._start_single_ai_job = Mock()
        v._on_batch_ai_finished()
        v._cleanup_batch_ai_thread(thread, worker)
        self.assertEqual(v._start_single_ai_job.call_count, 1)

    def test_decoded_pages_entering_range_are_scheduled(self):
        v = self.v
        v.reader = Mock(image_names=list(range(10)))
        v._image_cache = {i: (b'', self.image) for i in range(10)}
        v.index = 5
        v._maybe_prefetch_ai = Mock()
        v._schedule_prefetch()
        self.assertIn(6, [c.args[0] for c in v._maybe_prefetch_ai.call_args_list])

    def test_missing_generation_is_stale(self):
        self.assertTrue(self.v._is_ai_result_stale((0, 0, 'original')))

    def test_manual_target_works_at_100_percent(self):
        v = self.v
        v.ai_target_mode = 'manual'
        v.ai_target_width = v.ai_target_height = 128
        self.assertIsNotNone(v._resolve_ai_plan(QPixmap.fromImage(self.image), 32, 32, 0, False))

    def test_timeout_kills_and_reaps_single_process(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
            process = Mock()
            process.poll.return_value = None
            process.communicate.side_effect = [subprocess.TimeoutExpired('fake', 120), (b'', b'')]
            worker = ai_upscale.AIUpscaleWorker((0, 0, 'original'), self.image, 'realesrgan', 'realesrgan-x4plus-anime')
            with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale.subprocess, 'Popen', return_value=process):
                worker.run()
            process.kill.assert_called_once()
            self.assertEqual(process.communicate.call_count, 2)

    def fake_engine(self):
        FakeSingle.starts = []
        FakeSingle.failures = set()
        FakeBatch.fail = False
        for target, name, value in [(viewer, 'AIUpscaleWorker', FakeSingle),
                                    (viewer, 'BatchAIUpscaleWorker', FakeBatch),
                                    (viewer, 'is_engine_available', lambda _: True)]:
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def idle(self):
        return self.v._ai_thread_ref is None and self.v._batch_thread_ref is None and not self.v._ai_queue

    def populate(self, count):
        v = self.v
        v.reader = Mock(image_names=list(range(count)))
        v._image_cache = {i: (b'', self.image) for i in range(count)}
        v.original_pixmap = QPixmap.fromImage(self.image)
        v.passed_pages_keep_count = -1
        v.ai_prefetch_depth = -1

    def test_real_qthreads_drain_sixty_pages_with_bounded_batches(self):
        self.fake_engine()
        self.populate(60)
        self.v.render_current_pixmap()
        self.v._schedule_prefetch()
        spin_until(lambda: self.idle() and len(self.v._ai_cache) == 60)
        processed = [i for _, _, indices in FakeSingle.starts for i in indices]
        self.assertEqual(sorted(processed), list(range(60)))
        self.assertEqual(FakeSingle.starts[0][2], [0])
        self.assertEqual(FakeSingle.starts[1][2], [1])
        self.assertTrue(any(kind == 'batch' for kind, _, _ in FakeSingle.starts))
        self.assertLessEqual(max(len(indices) for _, _, indices in FakeSingle.starts), 8)

    def test_batch_failure_falls_back_to_single_and_finishes(self):
        self.fake_engine()
        FakeBatch.fail = True
        self.populate(12)
        self.v.render_current_pixmap()
        self.v._schedule_prefetch()
        spin_until(lambda: self.idle() and len(self.v._ai_cache) == 12)
        singles = [i for kind, _, indices in FakeSingle.starts if kind == 'single' for i in indices]
        self.assertEqual(sorted(singles), list(range(12)))

    def test_single_failure_is_not_retried_forever_by_refill(self):
        self.fake_engine()
        FakeSingle.failures = {3}
        self.populate(8)
        self.v.ai_batch_processing_enabled = False
        self.v.render_current_pixmap()
        self.v._schedule_prefetch()
        spin_until(self.idle)
        self.assertEqual(len(self.v._ai_cache), 7)
        self.assertIn((3, 0, 'original'), self.v._ai_failed_keys)
        self.assertEqual(len(FakeSingle.starts), 8)

    def test_reset_ignores_already_emitted_old_generation(self):
        self.fake_engine()
        self.populate(5)
        self.v.render_current_pixmap()
        time.sleep(.025)  # Signal is queued, but the GUI has not handled it yet.
        generation = self.v._archive_generation
        self.v.reset_ai_processing()
        spin_until(lambda: self.idle() and len(self.v._ai_cache) == 5)
        self.assertGreater(self.v._archive_generation, generation)
        self.assertEqual(len([1 for _, g, ids in FakeSingle.starts if g > generation and 0 in ids]), 1)

    def test_navigation_during_batch_recovers_current_page(self):
        self.fake_engine()
        self.populate(20)
        v = self.v
        v.render_current_pixmap()
        v._schedule_prefetch()
        spin_until(lambda: v._batch_thread_ref is not None)
        v.index = 10
        v._prune_ai_work_for_relevance()
        v.render_current_pixmap()
        spin_until(lambda: self.idle() and len(v._ai_cache) == 20)
        self.assertIn(10, v._ai_cache)

    def test_timeout_kills_and_reaps_batch_process(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
            process = Mock()
            process.poll.return_value = None
            process.communicate.side_effect = [subprocess.TimeoutExpired('fake', 120), (b'', b'')]
            worker = ai_upscale.BatchAIUpscaleWorker([((0, 0, 'original'), self.image)], 'realesrgan', 'realesrgan-x4plus-anime')
            with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale.subprocess, 'Popen', return_value=process):
                worker.run()
            process.kill.assert_called_once()
            self.assertEqual(process.communicate.call_count, 2)

    def test_folder_and_zip_decode_to_ai_pipeline(self):
        self.fake_engine()
        self.v.ai_prefetch_depth = -1
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'images'
            folder.mkdir()
            for i in range(15):
                self.image.save(str(folder / f'{i:02}.png'))
            (folder / '99.png').write_bytes(b'not an image')
            archive = Path(directory) / 'images.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                for path in folder.iterdir():
                    zf.write(path, path.name)
            for source in (folder, archive):
                with self.subTest(source=source.suffix or 'folder'):
                    self.v.open_archive(str(source))
                    spin_until(lambda: self.idle() and len(self.v._ai_cache) == 15
                               and not self.v._active_prefetch_threads)
                    self.assertEqual(self.v._prefetch_failed_indices, {15})
                    self.assertEqual(len(self.v._image_cache), 15)
            self.v._cleanup_archive_state()

    def test_reopen_while_old_decoder_for_same_index_is_running(self):
        self.fake_engine()
        self.v.ai_prefetch_depth = -1
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ('first', 'second'):
                folder = Path(directory) / name
                folder.mkdir()
                self.image.save(str(folder / '00.png'))
                paths.append(folder)
            original = viewer.ArchiveReader.read_independent
            def slow_read(reader, name):
                time.sleep(.015)
                return original(reader, name)
            with patch.object(viewer.ArchiveReader, 'read_independent', slow_read):
                for path in (paths[0], paths[1], paths[0]):
                    self.v.open_archive(str(path))
                spin_until(lambda: self.idle() and len(self.v._ai_cache) == 1
                           and not self.v._active_prefetch_threads)
            self.assertIsNone(self.v._waiting_for_index)
            self.assertEqual(self.v.current_archive_path, str(paths[0]))
            self.v._cleanup_archive_state()

    def test_background_off_still_processes_all_pages_singly(self):
        self.fake_engine()
        self.populate(12)
        self.v.ai_batch_processing_enabled = False
        self.v.render_current_pixmap()
        self.v._schedule_prefetch()
        spin_until(lambda: self.idle() and len(self.v._ai_cache) == 12)
        self.assertTrue(all(kind == 'single' for kind, _, _ in FakeSingle.starts))

    def test_moving_finite_window_fills_predecoded_pages(self):
        self.fake_engine()
        self.populate(15)
        v = self.v
        v.ai_prefetch_depth = 2
        v.render_current_pixmap()
        v._schedule_prefetch()
        spin_until(lambda: self.idle() and len(v._ai_cache) == 3)
        v.index = 5
        v.render_current_pixmap()
        v._schedule_prefetch()
        spin_until(lambda: self.idle() and all(i in v._ai_cache for i in range(3, 8)))

    def test_disabling_ai_cancels_active_batch_and_does_not_refill(self):
        self.fake_engine()
        self.populate(20)
        v = self.v
        v.render_current_pixmap()
        v._schedule_prefetch()
        spin_until(lambda: v._batch_thread_ref is not None)
        with patch.object(viewer, 'save_settings'):
            v.toggle_ai_upscale(False)
        spin_until(self.idle)
        self.assertEqual(v._ai_cache, {})

    def test_close_cancels_active_batch(self):
        self.fake_engine()
        self.populate(20)
        v = self.v
        v.render_current_pixmap()
        v._schedule_prefetch()
        spin_until(lambda: v._batch_thread_ref is not None)
        worker = v._batch_thread_ref[1]
        v.close()
        self.assertTrue(worker._cancel_requested)
        spin_until(self.idle)
        self.assertTrue(v._closing)

    def test_missing_batch_output_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (b'', b'')
            worker = ai_upscale.BatchAIUpscaleWorker([((0, 0, 'original'), self.image)], 'realesrgan', 'realesrgan-x4plus-anime')
            failed, finished = [], []
            worker.failed.connect(failed.append)
            worker.batch_finished.connect(lambda: finished.append(True))
            with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale.subprocess, 'Popen', return_value=process):
                worker.run()
            self.assertEqual(len(failed), 1)
            self.assertFalse(finished)

    def test_preparation_errors_emit_terminal_failure(self):
        for batch in (False, True):
            with self.subTest(batch=batch), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
                worker = (ai_upscale.BatchAIUpscaleWorker([((0, 0, 'original'), self.image)], 'realesrgan', 'realesrgan-x4plus-anime') if batch else
                          ai_upscale.AIUpscaleWorker((0, 0, 'original'), self.image, 'realesrgan', 'realesrgan-x4plus-anime'))
                failed = []
                worker.failed.connect(lambda *args: failed.append(args))
                with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale.tempfile, 'mkdtemp', side_effect=OSError('injected disk error')):
                    worker.run()
                self.assertEqual(len(failed), 1)

    def test_real_subprocess_png_roundtrip_single_and_batch_multipass(self):
        # Exercise actual process startup, PNG IO and multi-pass chaining, not GPU inference.
        script = '''import sys
from pathlib import Path
from PIL import Image
src,dst=map(Path,sys.argv[1:])
pairs=[(p,dst/p.name) for p in src.iterdir()] if src.is_dir() else [(src,dst)]
for a,b in pairs:
    with Image.open(a) as im:
        im.resize((im.width*2,im.height*2)).save(b)
'''
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
            def command(engine, exe, models, src, dst, name, **kwargs):
                return [sys.executable, '-c', script, str(src), str(dst)]
            with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale, 'build_command', command):
                for batch in (False, True):
                    with self.subTest(batch=batch):
                        worker = (ai_upscale.BatchAIUpscaleWorker([((i, 0, 'original'), self.image) for i in range(2)], 'realesrgan', 'realesrgan-x4plus-anime', passes=2) if batch else
                                  ai_upscale.AIUpscaleWorker((0, 0, 'original'), self.image, 'realesrgan', 'realesrgan-x4plus-anime', passes=2))
                        outputs, failures = [], []
                        (worker.item_finished if batch else worker.finished).connect(lambda key, img, *args: outputs.append(img))
                        worker.failed.connect(lambda *args: failures.append(args))
                        worker.run()
                        self.assertFalse(failures)
                        self.assertEqual(len(outputs), 2 if batch else 1)
                        self.assertTrue(all(img.width() == 128 for img in outputs))

    def test_real_timed_out_process_exits(self):
        original_popen = subprocess.Popen
        processes = []
        class ShortTimeoutProcess(original_popen):
            def communicate(self, input=None, timeout=None):
                return super().communicate(input=input, timeout=.03 if timeout is not None else None)
        def spawn(*args, **kwargs):
            proc = ShortTimeoutProcess(*args, **kwargs)
            processes.append(proc)
            return proc
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / ai_upscale.ENGINES['realesrgan']['exe_name']).touch()
            worker = ai_upscale.AIUpscaleWorker((0, 0, 'original'), self.image, 'realesrgan', 'realesrgan-x4plus-anime')
            failures = []
            worker.failed.connect(lambda *args: failures.append(args))
            with patch.object(ai_upscale, 'get_ai_upscale_dir', return_value=base), patch.object(ai_upscale, 'build_command', return_value=[sys.executable, '-c', 'import time;time.sleep(10)']), patch.object(ai_upscale.subprocess, 'Popen', spawn):
                worker.run()
            self.assertEqual(len(failures), 1)
            self.assertIsNotNone(processes[0].poll())

    def test_explicit_pass_modes_work_at_native_display_size(self):
        for mode, count in [('on', 1), ('count', 3)]:
            self.v.ai_upscale_mode = mode
            self.v.ai_upscale_fixed_count = count
            plan = self.v._resolve_ai_plan(QPixmap.fromImage(self.image), 32, 32, 0, False)
            self.assertEqual(plan[1], count)

    def test_old_worker_cannot_erase_new_same_key_metadata(self):
        v = self.v
        key = (0, 0, 'original')
        v._archive_generation = 2
        v._ai_job_generation[key] = 2
        v._ai_pending_key = key
        worker = ai_upscale.AIUpscaleWorker(key, self.image, 'realesrgan', 'realesrgan-x4plus-anime')
        worker._generation = 1
        worker.finished.connect(v._on_ai_upscale_finished)
        worker.failed.connect(v._on_ai_upscale_failed)
        worker.cancelled.connect(v._on_ai_upscale_cancelled)
        worker.finished.emit(key, self.image, 'realesrgan', 'realesrgan-x4plus-anime')
        worker.failed.emit(key, 'old failure')
        worker.cancelled.emit(key)
        self.assertEqual(v._ai_job_generation[key], 2)
        self.assertEqual(v._ai_pending_key, key)
        self.assertEqual(v._ai_cache, {})
        v._ai_pending_key = None

    def test_pruned_diff_job_releases_its_metadata(self):
        v = self.v
        key = (0, 0, 'original')
        v._ai_queue = [(self.image, key, 1, v.ai_upscale_model)]
        v._pending_diff_composite[key] = {'base_image': self.image}
        v._ai_processing_start_time[key] = time.time()
        v._ai_job_generation[key] = v._archive_generation
        v.index = 20
        v._prune_ai_work_for_relevance()
        self.assertFalse(v._ai_queue)
        self.assertNotIn(key, v._pending_diff_composite)
        self.assertNotIn(key, v._ai_processing_start_time)
        self.assertNotIn(key, v._ai_job_generation)

    def test_partial_batch_failure_retries_only_unfinished_items(self):
        self.fake_engine()
        self.populate(12)
        class PartialFailure(FakeBatch):
            def run(self):
                FakeSingle.starts.append(('batch', self._generation, [key[0] for key, _ in self.items]))
                key, image = self.items[0]
                self.item_finished.emit(key, image)
                self.failed.emit('injected partial output')
        with patch.object(viewer, 'BatchAIUpscaleWorker', PartialFailure):
            self.v.render_current_pixmap()
            self.v._schedule_prefetch()
            spin_until(lambda: self.idle() and len(self.v._ai_cache) == 12)
        completed_in_batch = [ids[0] for kind, _, ids in FakeSingle.starts if kind == 'batch']
        singles = [i for kind, _, ids in FakeSingle.starts if kind == 'single' for i in ids]
        self.assertFalse(set(completed_in_batch) & set(singles))

    def test_decoder_exception_emits_failure(self):
        reader = Mock()
        reader.read_independent.return_value = b'broken'
        worker = viewer.PrefetchWorker(reader, 0, 'bad.tlg', 'archive')
        failures = []
        worker.failed.connect(lambda *args: failures.append(args))
        with patch.object(viewer, 'decode_tlg', side_effect=ValueError('injected decoder error')):
            worker.run()
        self.assertEqual(failures, [('archive', 0)])

if __name__ == '__main__':
    unittest.main()

