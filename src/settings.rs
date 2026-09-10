use std::{fs, path::PathBuf};

#[derive(Clone)]
pub struct StoredSettings {
    pub natural_sort: bool,
    pub debug_logging: bool,
    pub fullscreen_exit_mode: usize,
    pub passed_pages_keep_count: i32,
    pub ai_enabled: bool,
    pub difference_mode: bool,
    pub difference_threshold: u8,
    pub difference_padding: u32,
}

impl Default for StoredSettings {
    fn default() -> Self {
        Self { natural_sort: true, debug_logging: false, fullscreen_exit_mode: 1,
            passed_pages_keep_count: -1, ai_enabled: true, difference_mode: false,
            difference_threshold: 0, difference_padding: 48 }
    }
}

impl StoredSettings {
    pub fn load() -> Self {
        let mut value = Self::default();
        let Ok(text) = fs::read_to_string(path()) else { return value };
        value.natural_sort = boolean(&text, "natural_sort").unwrap_or(value.natural_sort);
        value.debug_logging = boolean(&text, "debug_logging_enabled").unwrap_or(value.debug_logging);
        value.ai_enabled = boolean(&text, "ai_upscale_enabled").unwrap_or(value.ai_enabled);
        value.difference_mode = boolean(&text, "ai_difference_mode").unwrap_or(value.difference_mode);
        value.fullscreen_exit_mode = number(&text, "fullscreen_exit_mode_index").unwrap_or(value.fullscreen_exit_mode);
        value.passed_pages_keep_count = number(&text, "passed_pages_keep_count").unwrap_or(value.passed_pages_keep_count);
        value.difference_threshold = number(&text, "difference_threshold").unwrap_or(value.difference_threshold);
        value.difference_padding = number(&text, "difference_padding").unwrap_or(value.difference_padding);
        value
    }

    pub fn save(&self) {
        let json = format!(concat!("{{\n", "  \"settings_version\": 2,\n",
            "  \"natural_sort\": {},\n", "  \"debug_logging_enabled\": {},\n",
            "  \"fullscreen_exit_mode_index\": {},\n", "  \"passed_pages_keep_count\": {},\n",
            "  \"ai_upscale_enabled\": {},\n", "  \"ai_difference_mode\": {},\n",
            "  \"difference_threshold\": {},\n", "  \"difference_padding\": {}\n", "}}\n"),
            self.natural_sort, self.debug_logging, self.fullscreen_exit_mode,
            self.passed_pages_keep_count, self.ai_enabled, self.difference_mode,
            self.difference_threshold, self.difference_padding);
        let _ = fs::write(path(), json);
    }
}

fn path() -> PathBuf {
    std::env::current_exe().ok().and_then(|path| path.parent().map(|base| base.join("settings.json")))
        .unwrap_or_else(|| PathBuf::from("settings.json"))
}

fn field<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let rest = text.split_once(&format!("\"{key}\""))?.1.split_once(':')?.1.trim_start();
    Some(rest.split([',', '\n', '}']).next()?.trim())
}
fn boolean(text: &str, key: &str) -> Option<bool> { match field(text, key)? { "true" => Some(true), "false" => Some(false), _ => None } }
fn number<T: std::str::FromStr>(text: &str, key: &str) -> Option<T> { field(text, key)?.trim_matches('"').parse().ok() }

