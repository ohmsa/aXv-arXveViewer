use std::path::{Path, PathBuf};

#[derive(Clone)]
pub struct AiSettings {
    pub enabled: bool,
    pub difference_mode: bool,
    pub difference_threshold: u8,
    pub difference_padding: u32,
    pub executable: PathBuf,
}

impl Default for AiSettings {
    fn default() -> Self {
        Self {
            enabled: false,
            difference_mode: false,
            difference_threshold: 0,
            difference_padding: 48,
            executable: PathBuf::from("ai_upscale/realcugan-ncnn-vulkan.exe"),
        }
    }
}

impl AiSettings {
    pub fn available(&self, app_dir: &Path) -> bool {
        app_dir.join(&self.executable).is_file()
    }
}

// The external RealCUGAN adapter will live here. Its PNG boundary is deliberately
// kept out of the image library so a future in-process ncnn backend can consume
// RGBA buffers without changing archive and cache management.
