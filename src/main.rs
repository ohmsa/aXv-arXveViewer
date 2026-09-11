mod ai;
mod app;
mod difference;
mod folder;
mod library;
mod scheduler;
mod settings;
mod tlg;
mod tlg_codec;

use anyhow::Result;

fn main() -> Result<()> {
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_title("aXv")
            .with_inner_size([1280.0, 800.0]),
        renderer: eframe::Renderer::Wgpu,
        ..Default::default()
    };

    eframe::run_native(
        "aXv",
        options,
        Box::new(|cc| Ok(Box::new(app::AxvApp::new(cc)))),
    )
    .map_err(|error| anyhow::anyhow!(error.to_string()))
}
