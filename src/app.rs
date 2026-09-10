use crate::{ai::AiSettings, difference::{find_difference, find_difference_regions}, library::ImageLibrary, scheduler::processing_order};
use crate::folder::{EntryKind, FolderEntry};
use eframe::egui::{self, ColorImage, FontData, FontDefinitions, FontFamily, Key, TextureHandle, TextureOptions};
use std::{collections::{HashSet, VecDeque}, fs, path::{Path, PathBuf}, sync::{atomic::{AtomicU64, Ordering}, mpsc::Receiver, Arc}};

pub struct AxvApp {
    library: Option<ImageLibrary>,
    current: usize,
    generation: u64,
    texture: Option<TextureHandle>,
    texture_page: Option<(u64, usize, u8, bool)>,
    ai: AiSettings,
    status: String,
    fullscreen: bool,
    fit_to_window: bool,
    zoom: f32,
    show_about: bool,
    show_properties: bool,
    show_options: bool,
    options_tab: usize,
    path_text: String,
    rotation: u8,
    aspect_mode: usize,
    always_on_top: bool,
    auto_resize_window: bool,
    natural_sort: bool,
    debug_logging: bool,
    fullscreen_exit_mode: usize,
    passed_pages_keep_count: i32,
    show_password_manager: bool,
    password_text: String,
    history: Vec<(PathBuf, bool)>,
    history_index: usize,
    folder_path: Option<PathBuf>,
    folder_entries: Vec<FolderEntry>,
    ai_queue: VecDeque<usize>,
    ai_receiver: Option<Receiver<crate::ai::AiResult>>,
    ai_processing: Option<usize>,
    ai_processed: HashSet<usize>,
    last_ai_elapsed: Option<std::time::Duration>,
    keybinds: Vec<String>,
    show_pre_ai: bool,
    show_difference_region: bool,
    difference_regions: Vec<(crate::difference::DifferenceRect, (u32, u32))>,
    folder_icon_size: usize,
    active_generation: Arc<AtomicU64>,
    last_pointer_position: Option<egui::Pos2>,
    last_pointer_activity: std::time::Instant,
    cursor_hidden: bool,
}

impl AxvApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        cc.egui_ctx.set_visuals(egui::Visuals::light());
        let japanese_font_loaded = install_windows_japanese_font(&cc.egui_ctx);
        let saved = crate::settings::StoredSettings::load();
        let mut ai = AiSettings::default();
        ai.enabled = saved.ai_enabled;
        ai.difference_mode = saved.difference_mode;
        ai.difference_threshold = saved.difference_threshold;
        ai.difference_padding = saved.difference_padding;
        Self {
            library: None,
            current: 0,
            generation: 0,
            texture: None,
            texture_page: None,
            ai,
            status: if japanese_font_loaded {
                "フォルダ、ZIP、RAR、または画像を開いてください".to_owned()
            } else {
                "Japanese font was not found in C:\\Windows\\Fonts".to_owned()
            },
            fullscreen: false,
            fit_to_window: true,
            zoom: 1.0,
            show_about: false,
            show_properties: false,
            show_options: false,
            options_tab: 0,
            path_text: String::new(),
            rotation: 0,
            aspect_mode: 0,
            always_on_top: false,
            auto_resize_window: false,
            natural_sort: saved.natural_sort,
            debug_logging: saved.debug_logging,
            fullscreen_exit_mode: saved.fullscreen_exit_mode.min(1),
            passed_pages_keep_count: saved.passed_pages_keep_count,
            show_password_manager: false,
            password_text: String::new(),
            history: Vec::new(),
            history_index: 0,
            folder_path: None,
            folder_entries: Vec::new(),
            ai_queue: VecDeque::new(),
            ai_receiver: None,
            ai_processing: None,
            ai_processed: HashSet::new(),
            last_ai_elapsed: None,
            keybinds: keybind_rows().iter().map(|(_, key)| (*key).to_owned()).collect(),
            show_pre_ai: false,
            show_difference_region: false,
            difference_regions: Vec::new(),
            folder_icon_size: 1,
            active_generation: Arc::new(AtomicU64::new(0)),
            last_pointer_position: None,
            last_pointer_activity: std::time::Instant::now(),
            cursor_hidden: false,
        }
    }

    fn open_file_dialog(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .add_filter("画像・アーカイブ", &["png", "jpg", "jpeg", "webp", "bmp", "gif", "zip", "rar"])
            .pick_file()
        {
            self.open(path);
        }
    }

    fn open(&mut self, path: PathBuf) {
        self.open_path(path, true);
    }

    fn open_path(&mut self, path: PathBuf, record_history: bool) {
        self.generation = self.generation.wrapping_add(1);
        self.active_generation.store(self.generation, Ordering::Release);
        self.library = None;
        self.texture = None;
        self.texture_page = None;
        self.current = 0;
        self.ai_queue.clear();
        self.ai_receiver = None;
        self.ai_processing = None;
        self.ai_processed.clear();
        self.difference_regions.clear();
        self.folder_path = None;
        self.folder_entries.clear();
        self.path_text = path.display().to_string();
        match ImageLibrary::open(&path, self.generation, self.natural_sort) {
            Ok(library) => {
                self.status = format!("{}枚をデコードしました", library.pages.len());
                self.library = Some(library);
                self.refresh_ai_queue();
                if record_history {
                    if !self.history.is_empty() { self.history.truncate(self.history_index + 1); }
                    if self.history.last() != Some(&(path.clone(), false)) { self.history.push((path, false)); }
                    self.history_index = self.history.len().saturating_sub(1);
                }
            }
            Err(error) => self.status = error.to_string(),
        }
    }

    fn browse_folder(&mut self, path: PathBuf, record_history: bool) {
        match crate::folder::list(&path, self.natural_sort) {
            Ok(entries) => {
                self.generation = self.generation.wrapping_add(1);
                self.active_generation.store(self.generation, Ordering::Release);
                self.library = None;
                self.texture = None;
                self.texture_page = None;
                self.ai_receiver = None;
                self.ai_queue.clear();
                self.folder_entries = entries;
                self.folder_path = Some(path.clone());
                self.path_text = path.display().to_string();
                self.status = format!("{}項目", self.folder_entries.len());
                if record_history {
                    if !self.history.is_empty() { self.history.truncate(self.history_index + 1); }
                    if self.history.last() != Some(&(path.clone(), true)) { self.history.push((path, true)); }
                    self.history_index = self.history.len().saturating_sub(1);
                }
            }
            Err(error) => self.status = error.to_string(),
        }
    }

    fn open_folder_image(&mut self, image_path: PathBuf) {
        let Some(parent) = image_path.parent().map(Path::to_path_buf) else { return };
        let name = image_path.file_name().and_then(|name| name.to_str()).map(str::to_owned);
        self.open(parent);
        if let (Some(name), Some(library)) = (name, &self.library) {
            if let Some(index) = library.pages.iter().position(|page| page.name == name) { self.current = index; }
        }
    }

    fn go_back(&mut self) {
        if self.history_index > 0 {
            self.history_index -= 1;
            let (path, folder) = self.history[self.history_index].clone();
            if folder { self.browse_folder(path, false); } else { self.open_path(path, false); }
        }
    }

    fn go_forward(&mut self) {
        if self.history_index + 1 < self.history.len() {
            self.history_index += 1;
            let (path, folder) = self.history[self.history_index].clone();
            if folder { self.browse_folder(path, false); } else { self.open_path(path, false); }
        }
    }

    fn change_image_directory(&mut self, direction: isize) {
        let Some(library) = &self.library else { return };
        let page_directory = |name: &str| Path::new(name).parent().unwrap_or(Path::new("")).to_string_lossy().replace('\\', "/");
        let mut directories = Vec::<(String, usize)>::new();
        for (index, page) in library.pages.iter().enumerate() {
            let directory = page_directory(&page.name);
            if directories.last().is_none_or(|(last, _)| *last != directory) {
                directories.push((directory, index));
            }
        }
        let current_directory = page_directory(&library.pages[self.current].name);
        let Some(position) = directories.iter().position(|(directory, _)| *directory == current_directory) else { return };
        let next = position as isize + direction;
        if next < 0 || next >= directories.len() as isize {
            self.status = if direction < 0 { "最初の画像フォルダです" } else { "最後の画像フォルダです" }.to_owned();
            return;
        }
        let (directory, page) = directories[next as usize].clone();
        self.set_page(page);
        self.status = format!("画像フォルダ: {}", if directory.is_empty() { "最上層" } else { &directory });
    }

    fn change_page(&mut self, delta: isize) {
        let Some(length) = self.library.as_ref().map(|library| library.pages.len()) else { return };
        let last = length.saturating_sub(1) as isize;
        let mut next = (self.current as isize + delta).clamp(0, last);
        if self.ai.skip_low_res_display {
            while self.page_is_low_resolution(next as usize) {
                let candidate = next + delta.signum();
                if candidate < 0 || candidate > last { break; }
                next = candidate;
            }
        }
        self.current = next as usize;
        self.difference_regions.clear();
        self.evict_passed_pages();
        self.refresh_ai_queue();
    }

    fn set_page(&mut self, index: usize) {
        if let Some(library) = &self.library {
            self.current = index.min(library.pages.len().saturating_sub(1));
            self.difference_regions.clear();
            self.evict_passed_pages();
            self.refresh_ai_queue();
        }
    }

    fn evict_passed_pages(&mut self) {
        if self.passed_pages_keep_count < 0 { return; }
        let keep = self.passed_pages_keep_count as usize;
        let boundary = self.current.saturating_sub(keep);
        if let Some(library) = &mut self.library { library.purge_before_current_window(self.current, keep); }
        self.ai_processed.retain(|index| *index >= boundary);
    }

    fn refresh_ai_queue(&mut self) {
        let Some(library) = &self.library else { return };
        let processing = self.ai_processing;
        self.ai_queue = processing_order(library.pages.len(), self.current).into_iter()
            .filter(|index| !self.ai_processed.contains(index) && Some(*index) != processing)
            .filter(|index| !(self.ai.skip_low_res_ai && self.page_is_low_resolution(*index)))
            .collect();
    }

    fn page_is_low_resolution(&self, index: usize) -> bool {
        self.library.as_ref().and_then(|library| library.pages.get(index))
            .and_then(|page| page.dimensions())
            .is_some_and(|(width, height)| width < self.ai.skip_low_res_threshold || height < self.ai.skip_low_res_threshold)
    }

    fn toggle_difference_region(&mut self) {
        self.show_difference_region = !self.show_difference_region;
        self.difference_regions.clear();
        if self.show_difference_region && self.current > 0 {
            if let Some(library) = &self.library {
                if let (Ok(previous), Ok(current)) = (
                    library.pages[self.current - 1].decode_original(),
                    library.pages[self.current].decode_original(),
                ) {
                    let dimensions = current.dimensions();
                    self.difference_regions = find_difference_regions(&previous, &current, self.ai.difference_threshold, self.ai.difference_padding)
                        .into_iter().map(|region| (region, dimensions)).collect();
                }
            }
        }
        self.status = if self.show_difference_region { "差分処理範囲を表示（Vで解除）" } else { "差分処理範囲を非表示" }.to_owned();
    }

    fn reset_ai_processing(&mut self) {
        self.generation = self.generation.wrapping_add(1);
        self.active_generation.store(self.generation, Ordering::Release);
        self.ai_receiver = None;
        self.ai_processing = None;
        self.ai_processed.clear();
        if let Some(library) = &mut self.library {
            library.generation = self.generation;
            if let Err(error) = library.restore_originals() { self.status = error.to_string(); }
        }
        self.texture_page = None;
        self.refresh_ai_queue();
        self.status = "AIキューと処理結果をリセットしました".to_owned();
    }

    fn drive_ai(&mut self) {
        if let Some(receiver) = &self.ai_receiver {
            match receiver.try_recv() {
                Ok(message) => {
                    self.ai_receiver = None;
                    self.ai_processing = None;
                    self.last_ai_elapsed = Some(message.elapsed);
                    if message.generation == self.generation {
                        match message.result {
                            Ok(image) => {
                                if let Some(library) = &mut self.library {
                                    if let Some(page) = library.pages.get_mut(message.index) { page.replace_decoded(image); }
                                }
                                self.ai_processed.insert(message.index);
                                if message.index == self.current { self.texture_page = None; }
                                self.status = format!("AI処理完了: {}ページ", message.index + 1);
                            }
                            Err(error) => {
                                self.ai_processed.insert(message.index);
                                self.status = error;
                            }
                        }
                    }
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => return,
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    self.ai_receiver = None;
                    self.ai_processing = None;
                }
            }
        }
        if !self.ai.enabled || self.ai_receiver.is_some() { return; }
        if crate::ai::find_ai_dir().is_none() { return; }
        let Some(index) = self.ai_queue.pop_front() else { return };
        let Some(library) = &mut self.library else { return };
        let mut composition = None;
        let image = if self.ai.difference_mode && index > 0 && self.ai_processed.contains(&(index - 1)) {
            let previous_original = library.pages[index - 1].decode_original();
            let current_original = library.pages[index].decode_original();
            match (previous_original, current_original) {
                (Ok(previous_original), Ok(current_original)) => {
                    let regions = find_difference_regions(&previous_original, &current_original, self.ai.difference_threshold, self.ai.difference_padding);
                    if !regions.is_empty() {
                        let area = regions.iter().map(|rect| u64::from(rect.width) * u64::from(rect.height)).sum::<u64>();
                        let whole = u64::from(current_original.width()) * u64::from(current_original.height());
                        if regions.len() <= 8 && area * 100 < whole * 40 {
                            let crops = regions.into_iter().map(|rect| {
                                let crop = image::imageops::crop_imm(&current_original, rect.x, rect.y, rect.width, rect.height).to_image();
                                (crop, rect)
                            }).collect();
                            let Some(base) = library.pages[index - 1].decoded.as_ref().map(|image| image.as_ref().clone()) else { return };
                            composition = Some((base, crops));
                            Arc::new(current_original)
                        } else { Arc::new(current_original) }
                    } else {
                        let Some(base) = library.pages[index - 1].decoded.as_ref().map(|image| image.as_ref().clone()) else { return };
                        library.pages[index].replace_decoded(base);
                        self.ai_processed.insert(index);
                        self.refresh_ai_queue();
                        return;
                    }
                }
                _ => match library.pages[index].ensure_decoded() { Ok(image) => image, Err(_) => return },
            }
        } else {
            match library.pages[index].ensure_decoded() { Ok(image) => image, Err(_) => return }
        };
        self.ai_processing = Some(index);
        self.status = format!("AI処理中: {} / {}", index + 1, library.pages.len());
        self.ai_receiver = Some(crate::ai::start_upscale(self.generation, index, image, self.ai.clone(), composition, self.active_generation.clone()));
    }

    fn toggle_fullscreen(&mut self, ctx: &egui::Context) {
        self.fullscreen = !self.fullscreen;
        self.last_pointer_activity = std::time::Instant::now();
        self.cursor_hidden = false;
        ctx.send_viewport_cmd(egui::ViewportCommand::CursorVisible(true));
        ctx.send_viewport_cmd(egui::ViewportCommand::Fullscreen(self.fullscreen));
    }

    fn update_fullscreen_cursor(&mut self, ctx: &egui::Context) {
        if !self.fullscreen {
            if self.cursor_hidden {
                ctx.send_viewport_cmd(egui::ViewportCommand::CursorVisible(true));
                self.cursor_hidden = false;
            }
            return;
        }
        let position = ctx.input(|input| input.pointer.hover_pos());
        let moved = position != self.last_pointer_position;
        if moved || ctx.input(|input| input.pointer.any_down()) {
            self.last_pointer_position = position;
            self.last_pointer_activity = std::time::Instant::now();
            if self.cursor_hidden {
                ctx.send_viewport_cmd(egui::ViewportCommand::CursorVisible(true));
                self.cursor_hidden = false;
            }
        } else if !self.cursor_hidden && self.last_pointer_activity.elapsed() >= std::time::Duration::from_secs(3) {
            ctx.send_viewport_cmd(egui::ViewportCommand::CursorVisible(false));
            self.cursor_hidden = true;
        }
        ctx.request_repaint_after(std::time::Duration::from_millis(100));
    }

    fn set_zoom(&mut self, zoom: Option<f32>) {
        match zoom {
            Some(value) => {
                self.fit_to_window = false;
                self.zoom = value;
            }
            None => self.fit_to_window = true,
        }
    }

    fn rotate(&mut self, clockwise: bool) {
        self.rotation = if clockwise { (self.rotation + 1) % 4 } else { (self.rotation + 3) % 4 };
        self.texture_page = None;
    }

    fn show_context_menu(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        let has_pages = self.library.as_ref().is_some_and(|library| !library.pages.is_empty());
        if ui.button("開く...").clicked() {
            self.open_file_dialog();
            ui.close_menu();
        }
        if ui.button(if self.fullscreen { "全画面表示を終了  F11" } else { "全画面表示  F11" }).clicked() {
            self.toggle_fullscreen(ctx);
            ui.close_menu();
        }
        ui.separator();

        ui.add_enabled_ui(has_pages, |ui| {
            ui.menu_button("ページ", |ui| {
                if ui.button("次のページ  Left").clicked() { self.change_page(1); ui.close_menu(); }
                if ui.button("前のページ  Right").clicked() { self.change_page(-1); ui.close_menu(); }
                ui.separator();
                if ui.button("最初の画像  Home").clicked() { self.set_page(0); ui.close_menu(); }
                if ui.button("最後の画像  End").clicked() {
                    let last = self.library.as_ref().map_or(0, |library| library.pages.len().saturating_sub(1));
                    self.set_page(last);
                    ui.close_menu();
                }
            });
            ui.menu_button("拡大縮小", |ui| {
                if ui.selectable_label(!self.fit_to_window && self.zoom == 1.0, "実際のサイズ（100%）").clicked() { self.set_zoom(Some(1.0)); ui.close_menu(); }
                if ui.selectable_label(self.fit_to_window, "ウィンドウに合わせる").clicked() { self.set_zoom(None); ui.close_menu(); }
                ui.separator();
                for (label, scale) in [("200%", 2.0), ("300%", 3.0), ("400%", 4.0), ("500%", 5.0)] {
                    if ui.selectable_label(!self.fit_to_window && self.zoom == scale, label).clicked() {
                        self.set_zoom(Some(scale));
                        ui.close_menu();
                    }
                }
                ui.separator();
                if ui.button("右に90度回転  R").clicked() { self.rotate(true); ui.close_menu(); }
                if ui.button("左に90度回転  L").clicked() { self.rotate(false); ui.close_menu(); }
                ui.separator();
                if ui.button("全画面表示切替  F11").clicked() { self.toggle_fullscreen(ctx); ui.close_menu(); }
            });
            ui.menu_button("アスペクト比", |ui| {
                for (index, label) in ["オリジナル", "4:3", "16:9"].iter().enumerate() {
                    if ui.selectable_label(self.aspect_mode == index, *label).clicked() {
                        self.aspect_mode = index;
                        ui.close_menu();
                    }
                }
            });
        });

        ui.separator();
        ui.menu_button("表示", |ui| {
            if ui.checkbox(&mut self.always_on_top, "常に手前に表示").changed() {
                let level = if self.always_on_top { egui::WindowLevel::AlwaysOnTop } else { egui::WindowLevel::Normal };
                ctx.send_viewport_cmd(egui::ViewportCommand::WindowLevel(level));
            }
            ui.checkbox(&mut self.auto_resize_window, "ウィンドウサイズを画像に合わせる");
            ui.separator();
            if ui.add_enabled(has_pages, egui::Button::new("3枚前より古い画像を破棄  M")).clicked() {
                self.purge_old_images();
                ui.close_menu();
            }
            ui.separator();
            if ui.add_enabled(has_pages, egui::Button::new("プロパティ")).clicked() {
                self.show_properties = true;
                ui.close_menu();
            }
        });
        ui.separator();
        if ui.button("オプション...").clicked() {
            self.show_options = true;
            ui.close_menu();
        }
        ui.separator();
        if ui.button("バージョン情報...").clicked() { self.show_about = true; ui.close_menu(); }
        if ui.button("デバッグログを開く").clicked() {
            self.status = "デバッグログはまだ作成されていません".to_owned();
            ui.close_menu();
        }
        if ui.button("終了").clicked() { ctx.send_viewport_cmd(egui::ViewportCommand::Close); }
    }

    fn show_folder_context_menu(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        if ui.button("開く...").clicked() { self.open_file_dialog(); ui.close_menu(); }
        if ui.button(if self.fullscreen { "全画面表示を終了  F11" } else { "全画面表示  F11" }).clicked() { self.toggle_fullscreen(ctx); ui.close_menu(); }
        ui.separator();
        let parent = self.folder_path.as_ref().and_then(|path| path.parent()).map(Path::to_path_buf);
        if ui.add_enabled(parent.is_some(), egui::Button::new("上のフォルダへ")).clicked() {
            if let Some(parent) = parent { self.browse_folder(parent, true); }
            ui.close_menu();
        }
        ui.menu_button("アイコンサイズ", |ui| {
            for (index, label) in ["小", "中", "大", "特大（4K向け）"].iter().enumerate() {
                if ui.selectable_label(self.folder_icon_size == index, *label).clicked() { self.folder_icon_size = index; ui.close_menu(); }
            }
        });
        ui.separator();
        if ui.button("オプション...").clicked() { self.show_options = true; ui.close_menu(); }
        if ui.button("バージョン情報...").clicked() { self.show_about = true; ui.close_menu(); }
        if ui.button("終了").clicked() { ctx.send_viewport_cmd(egui::ViewportCommand::Close); }
    }

    fn ensure_texture(&mut self, ctx: &egui::Context) {
        let Some(library) = &mut self.library else { return };
        let key = (library.generation, self.current, self.rotation, self.show_pre_ai);
        if self.texture_page == Some(key) { return; }
        let image_result = if self.show_pre_ai { library.pages[self.current].decode_original().map(Arc::new) } else { library.pages[self.current].ensure_decoded() };
        match image_result {
            Ok(image) => {
                let rotated = match self.rotation {
                    1 => image::imageops::rotate90(image.as_ref()),
                    2 => image::imageops::rotate180(image.as_ref()),
                    3 => image::imageops::rotate270(image.as_ref()),
                    _ => image.as_ref().clone(),
                };
                let size = [rotated.width() as usize, rotated.height() as usize];
                let color = ColorImage::from_rgba_unmultiplied(size, rotated.as_raw());
                self.texture = Some(ctx.load_texture(format!("page-{}-{}-{}-{}", key.0, key.1, key.2, key.3), color, TextureOptions::LINEAR));
                self.texture_page = Some(key);
            }
            Err(error) => self.status = error.to_string(),
        }
    }

    fn purge_old_images(&mut self) {
        let Some(library) = &mut self.library else { return };
        let freed = library.purge_before_current_window(self.current, 3);
        self.status = format!("3枚前より古いデコード画像を破棄: {:.1} MiB", freed as f64 / 1_048_576.0);
    }

    fn difference_summary(&mut self) {
        let Some(library) = &mut self.library else { return };
        if self.current == 0 { return; }
        let (before, after) = library.pages.split_at_mut(self.current);
        let previous = before[self.current - 1].ensure_decoded();
        let current = after[0].ensure_decoded();
        if let (Ok(previous), Ok(current)) = (previous, current) {
            self.status = match find_difference(&previous, &current, self.ai.difference_threshold, self.ai.difference_padding) {
                Some(rect) => format!("差分候補: ({}, {}) {}×{}", rect.x, rect.y, rect.width, rect.height),
                None => "前の画像と同一です".to_owned(),
            };
        }
    }
}

fn install_windows_japanese_font(ctx: &egui::Context) -> bool {
    // Windows 10/11の標準日本語フォントを優先順に探す。同梱やコピーはせず、
    // 実行中のWindowsにインストール済みのフォントだけを読み込む。
    const CANDIDATES: &[&str] = &[
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\YuGothR.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        r"C:\Windows\Fonts\msgothic.ttc",
    ];

    let Some(bytes) = CANDIDATES.iter().find_map(|path| fs::read(Path::new(path)).ok()) else {
        return false;
    };

    let mut fonts = FontDefinitions::default();
    fonts.font_data.insert(
        "axv-japanese".to_owned(),
        FontData::from_owned(bytes).into(),
    );
    for family in [FontFamily::Proportional, FontFamily::Monospace] {
        fonts.families.entry(family).or_default().insert(0, "axv-japanese".to_owned());
    }
    ctx.set_fonts(fonts);
    true
}

impl eframe::App for AxvApp {
    fn on_exit(&mut self) {
        crate::settings::StoredSettings {
            natural_sort: self.natural_sort,
            debug_logging: self.debug_logging,
            fullscreen_exit_mode: self.fullscreen_exit_mode,
            passed_pages_keep_count: self.passed_pages_keep_count,
            ai_enabled: self.ai.enabled,
            difference_mode: self.ai.difference_mode,
            difference_threshold: self.ai.difference_threshold,
            difference_padding: self.ai.difference_padding,
        }.save();
    }

    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.drive_ai();
        self.update_fullscreen_cursor(ctx);
        if self.ai_receiver.is_some() { ctx.request_repaint_after(std::time::Duration::from_millis(50)); }
        let pressed = self.keybinds.iter().map(|key| shortcut_pressed(ctx, key)).collect::<Vec<_>>();
        let dropped = ctx.input(|input| input.raw.dropped_files.first().and_then(|file| file.path.clone()));
        if let Some(path) = dropped { self.open(path); }
        if ctx.input(|input| input.key_pressed(Key::PageDown) || input.key_pressed(Key::Space)) { self.change_page(1); }
        if ctx.input(|input| input.key_pressed(Key::PageUp)) { self.change_page(-1); }
        if pressed[0] { self.open_file_dialog(); }
        if pressed[1] { self.change_page(1); }
        if pressed[2] { self.change_page(-1); }
        if pressed[3] { self.set_page(0); }
        if pressed[4] { let last = self.library.as_ref().map_or(0, |library| library.pages.len().saturating_sub(1)); self.set_page(last); }
        if pressed[5] { self.change_image_directory(-1); }
        if pressed[6] { self.change_image_directory(1); }
        if pressed[7] { self.set_zoom(Some(1.0)); }
        if pressed[8] { self.set_zoom(None); }
        for (index, scale) in [(9, 2.0), (10, 3.0), (11, 4.0), (12, 5.0)] { if pressed[index] { self.set_zoom(Some(scale)); } }
        if pressed[13] { self.toggle_fullscreen(ctx); }
        if pressed[14] { self.rotate(true); }
        if pressed[15] { self.rotate(false); }
        if pressed[16] { self.aspect_mode = (self.aspect_mode + 1) % 3; }
        if pressed[17] { self.always_on_top = !self.always_on_top; ctx.send_viewport_cmd(egui::ViewportCommand::WindowLevel(if self.always_on_top { egui::WindowLevel::AlwaysOnTop } else { egui::WindowLevel::Normal })); }
        if pressed[18] { self.purge_old_images(); }
        if pressed[19] { ctx.send_viewport_cmd(egui::ViewportCommand::Close); }
        if ctx.input(|input| input.key_pressed(Key::D) && !input.modifiers.any()) {
            self.ai.denoise_mode = (self.ai.denoise_mode + 1) % 3;
            self.status = format!("デノイズ: {}", ["自動判定", "常にオン", "常にオフ"][self.ai.denoise_mode]);
        }
        if ctx.input(|input| input.key_pressed(Key::U) && !input.modifiers.any()) {
            self.ai.upscale_mode = (self.ai.upscale_mode + 1) % 6;
            self.status = format!("アップスケール方式: {}", self.ai.upscale_mode + 1);
        }
        if ctx.input(|input| input.key_pressed(Key::O) && !input.modifiers.any()) {
            self.show_pre_ai = !self.show_pre_ai;
            self.texture_page = None;
            self.status = if self.show_pre_ai { "AI処理前を表示" } else { "AI処理後を表示" }.to_owned();
        }
        if ctx.input(|input| input.key_pressed(Key::V) && !input.modifiers.any()) { self.toggle_difference_region(); }
        if ctx.input(|input| input.key_pressed(Key::E) && !input.modifiers.any()) { self.reset_ai_processing(); }

        if !self.fullscreen {
            egui::TopBottomPanel::top("navigation").exact_height(38.0).show(ctx, |ui| {
                ui.horizontal_centered(|ui| {
                    if ui.add_enabled(self.history_index > 0, egui::Button::new("◀").frame(false)).on_hover_text("戻る").clicked() { self.go_back(); }
                    if ui.add_enabled(self.history_index + 1 < self.history.len(), egui::Button::new("▶").frame(false)).on_hover_text("進む").clicked() { self.go_forward(); }
                    if ui.button("↑").on_hover_text("上のフォルダへ").clicked() {
                        let current = self.folder_path.as_ref().or_else(|| self.library.as_ref().map(|library| &library.source));
                        let parent = current.and_then(|path| path.parent()).map(Path::to_path_buf);
                        if let Some(parent) = parent { self.browse_folder(parent, true); }
                    }
                    let response = ui.add_sized([ui.available_width(), 26.0], egui::TextEdit::singleline(&mut self.path_text));
                    if response.lost_focus() && ui.input(|input| input.key_pressed(Key::Enter)) {
                        let path = PathBuf::from(self.path_text.clone());
                        if path.is_dir() { self.browse_folder(path, true); } else { self.open(path); }
                    }
                });
            });
            // Panels must be added before CentralPanel. Otherwise egui paints the
            // late panel on top of the image and hides its bottom edge.
            egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
                let page = self.library.as_ref().map_or_else(|| "0 / 0".to_owned(), |lib| format!("{} / {}", self.current + 1, lib.pages.len()));
                let memory = self.library.as_ref().map_or(0, ImageLibrary::decoded_bytes);
                ui.horizontal(|ui| {
                    ui.label(page);
                    ui.separator();
                    ui.label(format!("デコード済み {:.1} MiB", memory as f64 / 1_048_576.0));
                    ui.separator();
                    ui.label(&self.status);
                    if let Some(library) = &self.library {
                        ui.add_space((ui.available_width() - 250.0).max(0.0));
                        ui.add_sized([240.0, 18.0], egui::Slider::new(&mut self.current, 0..=library.pages.len().saturating_sub(1)).show_value(false));
                    }
                });
            });
        }

        self.ensure_texture(ctx);
        let mut activated_entry = None;
        let panel = egui::CentralPanel::default().frame(egui::Frame::NONE.fill(egui::Color32::BLACK)).show(ctx, |ui| {
            if self.folder_path.is_some() {
                egui::ScrollArea::vertical().show(ui, |ui| {
                    ui.spacing_mut().item_spacing = [12.0, 12.0].into();
                    ui.horizontal_wrapped(|ui| {
                        for entry in &self.folder_entries {
                            let kind = match entry.kind { EntryKind::Folder => "[フォルダ]", EntryKind::Archive => "[書庫]", EntryKind::Image => "[画像]" };
                            let sizes = [[110.0, 66.0], [150.0, 86.0], [190.0, 110.0], [250.0, 145.0]];
                            let response = ui.add_sized(sizes[self.folder_icon_size], egui::Button::new(format!("{kind}\n{}", entry.name)).wrap());
                            if response.double_clicked() { activated_entry = Some(entry.clone()); }
                        }
                    });
                });
            } else if let Some(texture) = &self.texture {
                let available = ui.available_size();
                let source = texture.size_vec2();
                let mut display_source = source;
                if self.aspect_mode == 1 { display_source.y = display_source.x * 3.0 / 4.0; }
                if self.aspect_mode == 2 { display_source.y = display_source.x * 9.0 / 16.0; }
                let scale = if self.fit_to_window {
                    (available.x / display_source.x).min(available.y / display_source.y)
                } else {
                    self.zoom
                };
                ui.centered_and_justified(|ui| {
                    let response = ui.image((texture.id(), display_source * scale));
                    if self.show_difference_region {
                        for (region, (source_width, source_height)) in self.difference_regions.iter().copied() {
                                let sx = response.rect.width() / source_width as f32;
                                let sy = response.rect.height() / source_height as f32;
                                let min = response.rect.min + egui::vec2(region.x as f32 * sx, region.y as f32 * sy);
                                let max = min + egui::vec2(region.width as f32 * sx, region.height as f32 * sy);
                                ui.painter().rect_stroke(egui::Rect::from_min_max(min, max), 0.0,
                                    egui::Stroke::new(3.0_f32, egui::Color32::YELLOW), egui::StrokeKind::Inside);
                        }
                    }
                });
            } else {
                ui.centered_and_justified(|ui| { ui.label("ここへファイルまたはフォルダをドロップ"); });
            }
            // CentralPanel's response is hover-only.  Put a click target above the
            // rendered image so single/double clicks work across the whole viewer.
            if self.folder_path.is_none() {
                Some(ui.interact(ui.max_rect(), ui.id().with("viewer-click-surface"), egui::Sense::click()))
            } else {
                None
            }
        });
        if let Some(entry) = activated_entry {
            match entry.kind {
                EntryKind::Folder => self.browse_folder(entry.path, true),
                EntryKind::Archive => self.open(entry.path),
                EntryKind::Image => self.open_folder_image(entry.path),
            }
        }
        let viewer_response = panel.inner.as_ref().unwrap_or(&panel.response);
        if self.folder_path.is_some() { panel.response.context_menu(|ui| self.show_folder_context_menu(ui, ctx)); }
        else { viewer_response.context_menu(|ui| self.show_context_menu(ui, ctx)); }
        if viewer_response.double_clicked() { self.toggle_fullscreen(ctx); }
        if viewer_response.clicked() && !viewer_response.double_clicked() {
            if let Some(pointer) = viewer_response.interact_pointer_pos() {
                let local = pointer - viewer_response.rect.min;
                let left = local.x < viewer_response.rect.width() / 2.0;
                let upper = local.y < viewer_response.rect.height() / 2.0;
                if upper {
                    self.change_page(if left { -1 } else { 1 });
                } else {
                    self.change_image_directory(if left { -1 } else { 1 });
                }
            }
        }
        if viewer_response.hovered() {
            let (scroll, ctrl) = ctx.input(|input| (input.raw_scroll_delta.y, input.modifiers.ctrl));
            if scroll != 0.0 {
                if ctrl {
                    let current = if self.fit_to_window { 1.0 } else { self.zoom };
                    self.set_zoom(Some((current + scroll.signum() * 0.1).clamp(0.1, 8.0)));
                } else if scroll > 0.0 { self.change_page(-1); } else { self.change_page(1); }
            }
        }

        if self.debug_logging {
            if let Some(library) = &self.library {
                if let Some(page) = library.pages.get(self.current) {
                    let current_bytes = page.decoded_bytes();
                    let state = if self.ai_processed.contains(&self.current) { "AI処理済み" } else { "未処理" };
                    let elapsed = self.last_ai_elapsed
                        .map(|value| format!("\n処理時間: {:.2}秒", value.as_secs_f64()))
                        .unwrap_or_default();
                    let processing = self.ai_processing
                        .map(|index| format!("\n処理中: {} / {}", index + 1, library.pages.len()))
                        .unwrap_or_default();
                    let text = format!(
                        "元ファイル: {:.1} MiB\n現在: {:.1} MiB ({}){}\nAI処理済み: {} / {}{}",
                        page.encoded_bytes() as f64 / 1_048_576.0,
                        current_bytes as f64 / 1_048_576.0,
                        state,
                        elapsed,
                        self.ai_processed.len(),
                        library.pages.len(),
                        processing,
                    );
                    let top = if self.fullscreen { 28.0 } else { 48.0 };
                    egui::Area::new("debug-overlay".into())
                        .order(egui::Order::Foreground)
                        .fixed_pos(egui::pos2(10.0, top))
                        .interactable(false)
                        .show(ctx, |ui| {
                            egui::Frame::NONE
                                .fill(egui::Color32::from_black_alpha(170))
                                .corner_radius(6.0)
                                .inner_margin(8.0)
                                .show(ui, |ui| {
                                    ui.colored_label(egui::Color32::from_rgb(100, 255, 110), text);
                                });
                        });
                }
            }
        }

        if self.show_about {
            egui::Window::new("バージョン情報")
                .open(&mut self.show_about)
                .collapsible(false)
                .resizable(false)
                .show(ctx, |ui| {
                    ui.heading("aXv");
                    ui.label("Rust generic viewer 0.1.0");
                    ui.label("画像・ZIP・RARビューア");
                });
        }

        if self.show_properties {
            egui::Window::new("プロパティ")
                .open(&mut self.show_properties)
                .collapsible(false)
                .show(ctx, |ui| {
                    if let Some(library) = &self.library {
                        egui::CollapsingHeader::new("アーカイブ").default_open(true).show(ui, |ui| {
                            ui.label(format!("パス: {}", library.source.display()));
                            ui.label(format!("形式: {}", library.source.extension().and_then(|v| v.to_str()).unwrap_or("フォルダ").to_uppercase()));
                            ui.label(format!("内包する画像数: {}", library.pages.len()));
                            ui.label("パスワード: なし（保護されていないアーカイブ）");
                        });
                        let page = &library.pages[self.current];
                        egui::CollapsingHeader::new("ファイル").default_open(true).show(ui, |ui| {
                            ui.label(format!("ファイル名: {}", page.name));
                            ui.label(format!("ページ: {} / {}", self.current + 1, library.pages.len()));
                            ui.label(format!("圧縮後のサイズ: {} bytes", page.encoded_bytes()));
                        });
                        egui::CollapsingHeader::new("画像").default_open(true).show(ui, |ui| {
                            if let Some((width, height)) = page.dimensions() { ui.label(format!("解像度（元画像）: {width} x {height} px")); }
                            ui.label("カラーモード: RGBA");
                            ui.label("ビット深度: 32 bit");
                            ui.label("アルファチャンネル: あり");
                        });
                        egui::CollapsingHeader::new("メモリ使用状況").default_open(true).show(ui, |ui| {
                            ui.label(format!("先読み済み（生画像）: {:.1} MiB", library.decoded_bytes() as f64 / 1_048_576.0));
                            ui.label("AIアップスケール済み: 0 ページ / 0 MiB");
                        });
                        egui::CollapsingHeader::new("表示設定").default_open(true).show(ui, |ui| {
                            ui.label(format!("拡大縮小: {}", if self.fit_to_window { "ウィンドウに合わせる".into() } else { format!("{:.0}%", self.zoom * 100.0) }));
                            ui.label(format!("アスペクト比: {}", ["オリジナル", "4:3", "16:9"][self.aspect_mode]));
                            ui.label(format!("回転: {}度", self.rotation * 90));
                            let queue = processing_order(library.pages.len(), self.current);
                            let preview = queue.iter().take(8).map(|index| (index + 1).to_string()).collect::<Vec<_>>().join(" → ");
                            ui.label(format!("AI処理予定順（先頭8件）: {preview}"));
                        });
                    } else {
                        ui.label("画像が開かれていません");
                    }
                });
        }

        if self.show_options {
            let mut close_options = false;
            let mut analyze_difference = false;
            let mut refresh_ai_after_options = false;
            egui::Window::new("オプション")
                .open(&mut self.show_options)
                .collapsible(false)
                .default_size([620.0, 520.0])
                .show(ctx, |ui| {
                    ui.horizontal(|ui| {
                        for (index, label) in ["一般", "AI処理", "キーバインド", "ショートカット一覧"].iter().enumerate() {
                            ui.selectable_value(&mut self.options_tab, index, *label);
                        }
                    });
                    ui.separator();
                    egui::ScrollArea::vertical().show(ui, |ui| match self.options_tab {
                        0 => {
                            ui.heading("一般");
                            ui.checkbox(&mut self.natural_sort, "ファイル名を自然順でソートする（page2 < page10）");
                            ui.checkbox(&mut self.debug_logging, "デバッグログを出力する（トラブル調査用、通常はオフでよい）");
                            if ui.button("デバッグログを開く").clicked() { self.status = "デバッグログはまだ作成されていません".into(); }
                            ui.horizontal(|ui| {
                                ui.label("全画面終了後:");
                                egui::ComboBox::from_id_salt("fullscreen-exit")
                                    .selected_text(["直前のウィンドウサイズ", "画像の100%サイズ"][self.fullscreen_exit_mode])
                                    .show_ui(ui, |ui| {
                                        ui.selectable_value(&mut self.fullscreen_exit_mode, 0, "直前のウィンドウサイズ");
                                        ui.selectable_value(&mut self.fullscreen_exit_mode, 1, "画像の100%サイズ");
                                    });
                            });
                            ui.horizontal(|ui| {
                                ui.label("通り過ぎたページを保持する数:");
                                ui.add(egui::DragValue::new(&mut self.passed_pages_keep_count).range(-1..=500));
                                if self.passed_pages_keep_count < 0 { ui.label("無制限（解放しない）"); }
                            });
                            ui.horizontal(|ui| {
                                ui.label("暗号化ZIP/RAR/7z用パスワード:");
                                if ui.button("パスワードを管理...").clicked() { self.show_password_manager = true; }
                            });
                        }
                        1 => {
                            ui.heading("AIアップスケール / ノイズ除去");
                            if crate::ai::find_ai_dir().is_none() {
                                ui.colored_label(egui::Color32::DARK_RED, "RealCUGANが見つかりません（ai_upscaleフォルダを確認してください）");
                            }
                            ui.checkbox(&mut self.ai.enabled, "拡大表示時・ノイズが多い時にAI処理を使う");
                            combo_row(ui, "デノイズ:", "denoise", &mut self.ai.denoise_mode, &["自動判定", "常にオン", "常にオフ"]);
                            combo_row(ui, "アップスケール:", "upscale", &mut self.ai.upscale_mode,
                                      &["目標解像度まで（既定）", "なし", "あり（1回のみ）", "固定回数", "手前で止めて拡大", "超えて縮小"]);
                            ui.horizontal(|ui| { ui.label("固定回数:"); ui.add_enabled(self.ai.upscale_mode == 3, egui::DragValue::new(&mut self.ai.fixed_count).range(1..=1)); });
                            ui.horizontal(|ui| {
                                ui.label("目標解像度:");
                                egui::ComboBox::from_id_salt("target-mode")
                                    .selected_text(if self.ai.target_manual { "手動（下の解像度に収まるまで拡大）" } else { "自動（現在のズーム/画面解像度に合わせる）" })
                                    .show_ui(ui, |ui| {
                                        ui.selectable_value(&mut self.ai.target_manual, false, "自動（現在のズーム/画面解像度に合わせる）");
                                        ui.selectable_value(&mut self.ai.target_manual, true, "手動（下の解像度に収まるまで拡大）");
                                    });
                            });
                            ui.add_enabled_ui(self.ai.target_manual, |ui| {
                                ui.horizontal(|ui| { ui.label("目標幅:"); ui.add(egui::DragValue::new(&mut self.ai.target_width).range(100..=8000).speed(100).suffix(" px（幅）")); });
                                ui.horizontal(|ui| { ui.label("目標高さ:"); ui.add(egui::DragValue::new(&mut self.ai.target_height).range(100..=8000).speed(100).suffix(" px（高さ）")); });
                            });
                            ui.checkbox(&mut self.ai.prefetch_all, "アーカイブ全体を先読み対象にする");
                            ui.horizontal(|ui| {
                                ui.label("AI先読み範囲（前後ページ数）:");
                                ui.add_enabled(!self.ai.prefetch_all, egui::DragValue::new(&mut self.ai.prefetch_depth).range(0..=50));
                            });
                            ui.label("表示中に D キーでデノイズ、U キーでアップスケールの方式を切り替えられます。");
                            let ai_skip_changed = ui.checkbox(&mut self.ai.skip_low_res_ai, "低解像度の画像はAI処理を飛ばす").changed();
                            ui.checkbox(&mut self.ai.skip_low_res_display, "低解像度の画像は表示も飛ばす");
                            ui.horizontal(|ui| {
                                ui.label("しきい値（幅または高さ）:");
                                ui.add(egui::DragValue::new(&mut self.ai.skip_low_res_threshold).range(1..=2000).suffix(" px 未満"));
                            });
                            if ai_skip_changed { refresh_ai_after_options = true; }
                            ui.checkbox(&mut self.ai.batch_processing, "背景の先読み分をまとめて処理する（バッチ処理）");
                            let engines = crate::ai::available_engines();
                            if !engines.is_empty() && !engines.iter().any(|(id, _)| *id == self.ai.engine) {
                                self.ai.engine = engines[0].0;
                            }
                            ui.horizontal(|ui| {
                                ui.label("エンジン:");
                                let selected = engines.iter().find(|(id, _)| *id == self.ai.engine).map(|(_, name)| *name).unwrap_or("利用可能なエンジンなし");
                                egui::ComboBox::from_id_salt("engine").selected_text(selected).show_ui(ui, |ui| {
                                    for (id, name) in &engines { ui.selectable_value(&mut self.ai.engine, *id, *name); }
                                });
                            });
                            let models: &[&str] = match self.ai.engine {
                                0 => &["realesrgan-x4plus-anime", "realesrgan-x4plus"],
                                1 => &["up2x-no-denoise", "up3x-no-denoise", "up4x-no-denoise"],
                                2 => &["scale2.0x_model", "noise3_model（ノイズ除去のみ）"],
                                4 => &["DF2K（4倍）"],
                                5 => &["SRMD（2倍）"],
                                _ => &["未実装"],
                            };
                            combo_row(ui, "モデル:", "model", &mut self.ai.model, models);
                            combo_row(ui, "使うGPU:", "gpu", &mut self.ai.gpu, &["自動選択（Vulkan）", "GPU 0", "GPU 1", "GPU 2", "GPU 3"]);
                            ui.label("ai_upscaleフォルダ内で検出できた実行ファイルだけを表示しています。");
                            ui.separator();
                            let difference_changed = ui.checkbox(&mut self.ai.difference_mode, "前画像との差分領域だけを処理する").changed();
                            ui.add_enabled_ui(self.ai.difference_mode, |ui| {
                                ui.horizontal(|ui| {
                                    ui.label("画素差の許容値");
                                    ui.add(egui::Slider::new(&mut self.ai.difference_threshold, 0..=32));
                                });
                                ui.horizontal(|ui| {
                                    ui.label("切り出し領域の余白");
                                    ui.add(egui::Slider::new(&mut self.ai.difference_padding, 0..=128).suffix(" px"));
                                });
                            });
                            if difference_changed && self.ai.difference_mode { analyze_difference = true; }
                            ui.label("初回は通常のスムーズ拡大を表示し、AI処理完了後に高精細な結果へ切り替えます。");
                        }
                        2 => {
                            ui.heading("キーバインド");
                            for (index, (label, _)) in keybind_rows().iter().enumerate() {
                                ui.horizontal(|ui| { ui.label(*label); ui.add(egui::TextEdit::singleline(&mut self.keybinds[index]).desired_width(160.0)); });
                            }
                            ui.separator();
                            if ui.button("すべてデフォルトに戻す").clicked() {
                                self.keybinds = keybind_rows().iter().map(|(_, key)| (*key).to_owned()).collect();
                            }
                        }
                        _ => {
                            ui.heading("設定変更可能なショートカット（「キーバインド」タブで変更可）");
                            for (label, key) in keybind_rows() { ui.horizontal(|ui| { ui.label(label); ui.label(key); }); }
                            ui.separator();
                            ui.heading("固定のショートカット／マウス操作（変更不可）");
                            for (label, key) in fixed_shortcut_rows() { ui.horizontal(|ui| { ui.label(label); ui.label(key); }); }
                        }
                    });
                    ui.separator();
                    ui.horizontal(|ui| {
                        if ui.button("OK").clicked() { close_options = true; }
                        if ui.button("キャンセル").clicked() { close_options = true; }
                    });
                });
            if close_options { self.show_options = false; }
            if analyze_difference { self.difference_summary(); }
            if refresh_ai_after_options { self.refresh_ai_queue(); }
        }

        if self.show_password_manager {
            egui::Window::new("パスワードの管理")
                .open(&mut self.show_password_manager)
                .default_size([400.0, 300.0])
                .show(ctx, |ui| {
                    ui.label("1行に1つ、パスワードを入力してください。");
                    ui.label("資格情報マネージャーへの保存は移植中のため、このセッション中のみ保持します。");
                    ui.add_sized(ui.available_size(), egui::TextEdit::multiline(&mut self.password_text));
                });
        }

    }
}

fn combo_row(ui: &mut egui::Ui, label: &str, id: &str, value: &mut usize, choices: &[&str]) {
    *value = (*value).min(choices.len().saturating_sub(1));
    ui.horizontal(|ui| {
        ui.label(label);
        egui::ComboBox::from_id_salt(id).selected_text(choices[*value]).show_ui(ui, |ui| {
            for (index, choice) in choices.iter().enumerate() { ui.selectable_value(value, index, *choice); }
        });
    });
}

fn shortcut_pressed(ctx: &egui::Context, shortcut: &str) -> bool {
    let normalized = shortcut.trim().to_ascii_uppercase().replace(' ', "");
    if normalized.is_empty() { return false; }
    let parts = normalized.split('+').collect::<Vec<_>>();
    let key_name = parts.last().copied().unwrap_or_default();
    let key = match key_name {
        "LEFT" => Key::ArrowLeft, "RIGHT" => Key::ArrowRight,
        "UP" => Key::ArrowUp, "DOWN" => Key::ArrowDown,
        "PAGEUP" => Key::PageUp, "PAGEDOWN" => Key::PageDown,
        "HOME" => Key::Home, "END" => Key::End, "SPACE" => Key::Space,
        "ESC" | "ESCAPE" => Key::Escape, "ENTER" | "RETURN" => Key::Enter,
        "F11" => Key::F11, "," | "COMMA" => Key::Comma, "." | "PERIOD" => Key::Period,
        "0" => Key::Num0, "1" => Key::Num1, "2" => Key::Num2, "3" => Key::Num3,
        "4" => Key::Num4, "5" => Key::Num5, "6" => Key::Num6, "7" => Key::Num7,
        "8" => Key::Num8, "9" => Key::Num9,
        "A" => Key::A, "B" => Key::B, "C" => Key::C, "D" => Key::D,
        "E" => Key::E, "F" => Key::F, "G" => Key::G, "H" => Key::H,
        "I" => Key::I, "J" => Key::J, "K" => Key::K, "L" => Key::L,
        "M" => Key::M, "N" => Key::N, "O" => Key::O, "P" => Key::P,
        "Q" => Key::Q, "R" => Key::R, "S" => Key::S, "T" => Key::T,
        "U" => Key::U, "V" => Key::V, "W" => Key::W, "X" => Key::X,
        "Y" => Key::Y, "Z" => Key::Z,
        _ => return false,
    };
    let need_ctrl = parts[..parts.len().saturating_sub(1)].iter().any(|part| matches!(*part, "CTRL" | "CONTROL"));
    let need_shift = parts[..parts.len().saturating_sub(1)].contains(&"SHIFT");
    let need_alt = parts[..parts.len().saturating_sub(1)].contains(&"ALT");
    ctx.input(|input| input.key_pressed(key)
        && input.modifiers.ctrl == need_ctrl
        && input.modifiers.shift == need_shift
        && input.modifiers.alt == need_alt)
}

fn keybind_rows() -> [(&'static str, &'static str); 20] {
    [
        ("開く...", "Ctrl+O"), ("次のページ", "Left"), ("前のページ", "Right"),
        ("最初のページ", "Home"), ("最後のページ", "End"), ("前のディレクトリ", ","),
        ("次のディレクトリ", "."), ("実際のサイズ（100%）", "Ctrl+1"),
        ("ウィンドウに合わせる", "Ctrl+0"), ("200%", "Ctrl+2"), ("300%", "Ctrl+3"),
        ("400%", "Ctrl+4"), ("500%", "Ctrl+5"), ("全画面表示切替", "F11"),
        ("右に90度回転", "R"), ("左に90度回転", "L"),
        ("アスペクト比切替（順に切替）", "Ctrl+A"), ("常に手前に表示切替", "Ctrl+T"),
        ("古い画像を破棄", "M"), ("終了", "Esc"),
    ]
}

fn fixed_shortcut_rows() -> [(&'static str, &'static str); 12] {
    [
        ("次の画像へ", "Space"), ("デノイズモード切替", "D"),
        ("アップスケールモード切替", "U"), ("AI処理前／後の比較表示切替", "O"),
        ("AIキュー／キャッシュをリセット", "E"),
        ("差分処理範囲の表示切替", "V"),
        ("前の画像／次の画像", "画面クリック（左半分／右半分）"),
        ("前のフォルダ／次のフォルダ", ", / ."),
        ("上のフォルダへ", "右クリックメニューまたは上ボタン"),
        ("ダブルクリック", "全画面表示の切替"), ("マウスホイール", "ページ移動／Ctrlでズーム"),
        ("右クリック", "メニューを開く"),
    ]
}
