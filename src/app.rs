use crate::{ai::AiSettings, difference::find_difference, library::ImageLibrary};
use eframe::egui::{self, ColorImage, Key, TextureHandle, TextureOptions};
use std::path::PathBuf;

pub struct AxvApp {
    library: Option<ImageLibrary>,
    current: usize,
    generation: u64,
    texture: Option<TextureHandle>,
    texture_page: Option<(u64, usize)>,
    ai: AiSettings,
    status: String,
    fullscreen: bool,
}

impl AxvApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        cc.egui_ctx.set_visuals(egui::Visuals::dark());
        Self {
            library: None,
            current: 0,
            generation: 0,
            texture: None,
            texture_page: None,
            ai: AiSettings::default(),
            status: "フォルダ、ZIP、RAR、または画像を開いてください".to_owned(),
            fullscreen: false,
        }
    }

    fn open(&mut self, path: PathBuf) {
        self.generation = self.generation.wrapping_add(1);
        self.library = None;
        self.texture = None;
        self.texture_page = None;
        self.current = 0;
        match ImageLibrary::open(&path, self.generation) {
            Ok(library) => {
                self.status = format!("{}枚をデコードしました", library.pages.len());
                self.library = Some(library);
            }
            Err(error) => self.status = error.to_string(),
        }
    }

    fn change_page(&mut self, delta: isize) {
        let Some(library) = &self.library else { return };
        let last = library.pages.len().saturating_sub(1) as isize;
        self.current = (self.current as isize + delta).clamp(0, last) as usize;
    }

    fn ensure_texture(&mut self, ctx: &egui::Context) {
        let Some(library) = &mut self.library else { return };
        let key = (library.generation, self.current);
        if self.texture_page == Some(key) { return; }
        match library.pages[self.current].ensure_decoded() {
            Ok(image) => {
                let size = [image.width() as usize, image.height() as usize];
                let color = ColorImage::from_rgba_unmultiplied(size, image.as_raw());
                self.texture = Some(ctx.load_texture(format!("page-{}-{}", key.0, key.1), color, TextureOptions::LINEAR));
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

impl eframe::App for AxvApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let dropped = ctx.input(|input| input.raw.dropped_files.first().and_then(|file| file.path.clone()));
        if let Some(path) = dropped { self.open(path); }
        if ctx.input(|input| input.key_pressed(Key::ArrowRight) || input.key_pressed(Key::PageDown)) { self.change_page(1); }
        if ctx.input(|input| input.key_pressed(Key::ArrowLeft) || input.key_pressed(Key::PageUp)) { self.change_page(-1); }
        if ctx.input(|input| input.key_pressed(Key::M)) { self.purge_old_images(); }
        if ctx.input(|input| input.key_pressed(Key::F11)) {
            self.fullscreen = !self.fullscreen;
            ctx.send_viewport_cmd(egui::ViewportCommand::Fullscreen(self.fullscreen));
        }

        egui::TopBottomPanel::top("toolbar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("開く").clicked() {
                    if let Some(path) = rfd::FileDialog::new().pick_file() { self.open(path); }
                }
                if ui.button("フォルダ").clicked() {
                    if let Some(path) = rfd::FileDialog::new().pick_folder() { self.open(path); }
                }
                ui.separator();
                ui.checkbox(&mut self.ai.enabled, "AIアップスケール");
                if ui.checkbox(&mut self.ai.difference_mode, "差分領域のみ").changed() && self.ai.difference_mode {
                    self.difference_summary();
                }
                if ui.button("古い画像を破棄 (M)").clicked() { self.purge_old_images(); }
            });
        });

        self.ensure_texture(ctx);
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(egui::Color32::BLACK)).show(ctx, |ui| {
            if let Some(texture) = &self.texture {
                let available = ui.available_size();
                let source = texture.size_vec2();
                let scale = (available.x / source.x).min(available.y / source.y);
                ui.centered_and_justified(|ui| { ui.image((texture.id(), source * scale)); });
            } else {
                ui.centered_and_justified(|ui| { ui.label("ここへファイルまたはフォルダをドロップ"); });
            }
        });

        egui::TopBottomPanel::bottom("status").show(ctx, |ui| {
            let page = self.library.as_ref().map_or_else(|| "0 / 0".to_owned(), |lib| format!("{} / {}", self.current + 1, lib.pages.len()));
            let memory = self.library.as_ref().map_or(0, ImageLibrary::decoded_bytes);
            ui.horizontal(|ui| {
                ui.label(page);
                ui.separator();
                ui.label(format!("デコード済み {:.1} MiB", memory as f64 / 1_048_576.0));
                ui.separator();
                ui.label(&self.status);
            });
        });
    }
}
