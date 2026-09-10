use crate::{ai::AiSettings, difference::find_difference, library::ImageLibrary};
use eframe::egui::{self, ColorImage, FontData, FontDefinitions, FontFamily, Key, TextureHandle, TextureOptions};
use std::{fs, path::{Path, PathBuf}};

pub struct AxvApp {
    library: Option<ImageLibrary>,
    current: usize,
    generation: u64,
    texture: Option<TextureHandle>,
    texture_page: Option<(u64, usize)>,
    ai: AiSettings,
    status: String,
    fullscreen: bool,
    fit_to_window: bool,
    zoom: f32,
    show_about: bool,
    show_properties: bool,
    show_options: bool,
}

impl AxvApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        cc.egui_ctx.set_visuals(egui::Visuals::dark());
        let japanese_font_loaded = install_windows_japanese_font(&cc.egui_ctx);
        Self {
            library: None,
            current: 0,
            generation: 0,
            texture: None,
            texture_page: None,
            ai: AiSettings::default(),
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

    fn open_folder_dialog(&mut self) {
        if let Some(path) = rfd::FileDialog::new().pick_folder() {
            self.open(path);
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

    fn set_page(&mut self, index: usize) {
        if let Some(library) = &self.library {
            self.current = index.min(library.pages.len().saturating_sub(1));
        }
    }

    fn toggle_fullscreen(&mut self, ctx: &egui::Context) {
        self.fullscreen = !self.fullscreen;
        ctx.send_viewport_cmd(egui::ViewportCommand::Fullscreen(self.fullscreen));
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

    fn show_context_menu(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        let has_pages = self.library.as_ref().is_some_and(|library| !library.pages.is_empty());
        if ui.button("開く...").clicked() {
            self.open_file_dialog();
            ui.close_menu();
        }
        if ui.button("フォルダを開く...").clicked() {
            self.open_folder_dialog();
            ui.close_menu();
        }
        if ui.button(if self.fullscreen { "全画面表示を終了  F11" } else { "全画面表示  F11" }).clicked() {
            self.toggle_fullscreen(ctx);
            ui.close_menu();
        }
        ui.separator();

        ui.add_enabled_ui(has_pages, |ui| {
            ui.menu_button("ページ", |ui| {
                if ui.button("次の画像  → / PageDown").clicked() { self.change_page(1); ui.close_menu(); }
                if ui.button("前の画像  ← / PageUp").clicked() { self.change_page(-1); ui.close_menu(); }
                ui.separator();
                if ui.button("最初の画像  Home").clicked() { self.set_page(0); ui.close_menu(); }
                if ui.button("最後の画像  End").clicked() {
                    let last = self.library.as_ref().map_or(0, |library| library.pages.len().saturating_sub(1));
                    self.set_page(last);
                    ui.close_menu();
                }
            });
            ui.menu_button("拡大縮小", |ui| {
                if ui.selectable_label(self.fit_to_window, "ウィンドウに合わせる").clicked() { self.set_zoom(None); ui.close_menu(); }
                for (label, scale) in [("100%", 1.0), ("200%", 2.0), ("300%", 3.0), ("400%", 4.0), ("500%", 5.0)] {
                    if ui.selectable_label(!self.fit_to_window && self.zoom == scale, label).clicked() {
                        self.set_zoom(Some(scale));
                        ui.close_menu();
                    }
                }
            });
        });

        ui.menu_button("表示・処理", |ui| {
            ui.checkbox(&mut self.ai.enabled, "AIアップスケール");
            if ui.checkbox(&mut self.ai.difference_mode, "差分領域のみ").changed() && self.ai.difference_mode {
                self.difference_summary();
            }
            ui.separator();
            if ui.add_enabled(has_pages, egui::Button::new("3枚前より古い画像を破棄  M")).clicked() {
                self.purge_old_images();
                ui.close_menu();
            }
        });
        ui.separator();
        if ui.add_enabled(has_pages, egui::Button::new("プロパティ...")).clicked() {
            self.show_properties = true;
            ui.close_menu();
        }
        if ui.button("オプション...").clicked() {
            self.show_options = true;
            ui.close_menu();
        }
        ui.separator();
        if ui.button("バージョン情報...").clicked() { self.show_about = true; ui.close_menu(); }
        if ui.button("終了").clicked() { ctx.send_viewport_cmd(egui::ViewportCommand::Close); }
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
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        let dropped = ctx.input(|input| input.raw.dropped_files.first().and_then(|file| file.path.clone()));
        if let Some(path) = dropped { self.open(path); }
        if ctx.input(|input| input.key_pressed(Key::ArrowRight) || input.key_pressed(Key::PageDown)) { self.change_page(1); }
        if ctx.input(|input| input.key_pressed(Key::ArrowLeft) || input.key_pressed(Key::PageUp)) { self.change_page(-1); }
        if ctx.input(|input| input.key_pressed(Key::M)) { self.purge_old_images(); }
        if ctx.input(|input| input.key_pressed(Key::Home)) { self.set_page(0); }
        if ctx.input(|input| input.key_pressed(Key::End)) {
            let last = self.library.as_ref().map_or(0, |library| library.pages.len().saturating_sub(1));
            self.set_page(last);
        }
        if ctx.input(|input| input.key_pressed(Key::F11)) {
            self.toggle_fullscreen(ctx);
        }

        egui::TopBottomPanel::top("toolbar").show(ctx, |ui| {
            ui.horizontal(|ui| {
                if ui.button("開く").clicked() {
                    self.open_file_dialog();
                }
                if ui.button("フォルダ").clicked() {
                    self.open_folder_dialog();
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
        let panel = egui::CentralPanel::default().frame(egui::Frame::NONE.fill(egui::Color32::BLACK)).show(ctx, |ui| {
            if let Some(texture) = &self.texture {
                let available = ui.available_size();
                let source = texture.size_vec2();
                let scale = if self.fit_to_window {
                    (available.x / source.x).min(available.y / source.y)
                } else {
                    self.zoom
                };
                ui.centered_and_justified(|ui| { ui.image((texture.id(), source * scale)); });
            } else {
                ui.centered_and_justified(|ui| { ui.label("ここへファイルまたはフォルダをドロップ"); });
            }
        });
        panel.response.context_menu(|ui| self.show_context_menu(ui, ctx));

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
                        ui.label(format!("入力: {}", library.source.display()));
                        ui.label(format!("画像数: {}", library.pages.len()));
                        ui.label(format!("現在: {}", library.pages[self.current].name));
                        ui.label(format!("デコード済み: {:.1} MiB", library.decoded_bytes() as f64 / 1_048_576.0));
                    } else {
                        ui.label("画像が開かれていません");
                    }
                });
        }

        if self.show_options {
            egui::Window::new("オプション")
                .open(&mut self.show_options)
                .collapsible(false)
                .resizable(false)
                .show(ctx, |ui| {
                    ui.heading("AI処理");
                    ui.checkbox(&mut self.ai.enabled, "AIアップスケールを有効にする");
                    ui.checkbox(&mut self.ai.difference_mode, "前画像との差分領域だけを処理する");
                    ui.add_enabled_ui(self.ai.difference_mode, |ui| {
                        ui.horizontal(|ui| {
                            ui.label("画素差の許容値");
                            ui.add(egui::Slider::new(&mut self.ai.difference_threshold, 0..=32));
                        });
                        ui.horizontal(|ui| {
                            ui.label("切り出し領域の余白");
                            ui.add(egui::Slider::new(&mut self.ai.difference_padding, 0..=128).suffix(" px"));
                        });
                        ui.label("PNG・TLGなどの可逆画像は許容値0を推奨します。");
                    });
                    ui.separator();
                    ui.heading("メモリ");
                    ui.label("Mキー: 現在位置の3枚前より古いデコード画像を破棄");
                    ui.label("元ファイルと再デコード用データは削除しません。");
                });
        }

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
