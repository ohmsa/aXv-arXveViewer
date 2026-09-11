use std::{fs, path::PathBuf};

#[derive(Clone)]
pub struct StoredSettings {
    pub natural_sort: bool, pub debug_logging: bool, pub fullscreen_exit_mode: usize,
    pub passed_pages_keep_count: i32, pub always_on_top: bool, pub auto_resize_window: bool,
    pub fit_to_window: bool, pub zoom_percent: u32, pub aspect_mode: usize, pub rotation: u8,
    pub folder_icon_size: usize, pub keybinds: Vec<String>, pub ai_enabled: bool,
    pub difference_mode: bool, pub difference_strategy: usize, pub difference_threshold: u8, pub difference_padding: u32,
    pub ai_engine: usize, pub ai_model: usize, pub denoise_mode: usize, pub upscale_mode: usize,
    pub fixed_count: u32, pub target_manual: bool, pub target_width: u32, pub target_height: u32,
    pub prefetch_all: bool, pub prefetch_depth: u32, pub skip_low_res_ai: bool,
    pub skip_low_res_display: bool, pub skip_low_res_threshold: u32,
    pub batch_processing: bool, pub gpu: usize,
    pub gpu_cache_limit_mib: u32,
}

impl Default for StoredSettings {
    fn default() -> Self { Self {
        natural_sort: true, debug_logging: false, fullscreen_exit_mode: 1, passed_pages_keep_count: -1,
        always_on_top: false, auto_resize_window: false, fit_to_window: true, zoom_percent: 100,
        aspect_mode: 0, rotation: 0, folder_icon_size: 1, keybinds: Vec::new(), ai_enabled: true,
        difference_mode: false, difference_strategy: 0, difference_threshold: 0, difference_padding: 48, ai_engine: 1,
        ai_model: 0, denoise_mode: 0, upscale_mode: 0, fixed_count: 1, target_manual: false,
        target_width: 1920, target_height: 1080, prefetch_all: true, prefetch_depth: 5,
        skip_low_res_ai: true, skip_low_res_display: false, skip_low_res_threshold: 300,
        batch_processing: true, gpu: 0, gpu_cache_limit_mib: 1536,
    } }
}

impl StoredSettings {
    pub fn load() -> Self {
        let mut v = Self::default();
        let Ok(text) = fs::read_to_string(path()) else { return v };
        macro_rules! n { ($f:ident,$k:literal) => { v.$f = number(&text,$k).unwrap_or(v.$f); }; }
        macro_rules! b { ($f:ident,$k:literal) => { v.$f = boolean(&text,$k).unwrap_or(v.$f); }; }
        b!(natural_sort,"natural_sort"); b!(debug_logging,"debug_logging_enabled"); n!(fullscreen_exit_mode,"fullscreen_exit_mode_index");
        n!(passed_pages_keep_count,"passed_pages_keep_count"); b!(always_on_top,"always_on_top"); b!(auto_resize_window,"auto_resize_window");
        b!(fit_to_window,"fit_to_window"); n!(zoom_percent,"zoom_percent"); n!(aspect_mode,"aspect_mode_index"); n!(rotation,"rotation_quarters");
        n!(folder_icon_size,"folder_icon_size_index"); b!(ai_enabled,"ai_upscale_enabled"); b!(difference_mode,"ai_difference_mode");
        n!(difference_strategy,"difference_strategy");
        if v.difference_strategy == 0 && v.difference_mode { v.difference_strategy = 1; }
        n!(difference_threshold,"difference_threshold"); n!(difference_padding,"difference_padding"); n!(ai_engine,"ai_engine_index");
        n!(ai_model,"ai_model_index"); n!(denoise_mode,"denoise_mode_index"); n!(upscale_mode,"upscale_mode_index"); n!(fixed_count,"fixed_count");
        b!(target_manual,"target_manual"); n!(target_width,"target_width"); n!(target_height,"target_height"); b!(prefetch_all,"prefetch_all");
        n!(prefetch_depth,"prefetch_depth"); b!(skip_low_res_ai,"skip_low_res_ai"); b!(skip_low_res_display,"skip_low_res_display");
        n!(skip_low_res_threshold,"skip_low_res_threshold"); b!(batch_processing,"batch_processing"); n!(gpu,"gpu_index");
        n!(gpu_cache_limit_mib,"gpu_cache_limit_mib");
        v.keybinds = (0..20).filter_map(|i| string(&text, &format!("keybind_{i}"))).collect();
        v
    }

    pub fn save(&self) {
        let fields = [
            ("settings_version","3".into()), ("natural_sort",self.natural_sort.to_string()), ("debug_logging_enabled",self.debug_logging.to_string()),
            ("fullscreen_exit_mode_index",self.fullscreen_exit_mode.to_string()), ("passed_pages_keep_count",self.passed_pages_keep_count.to_string()),
            ("always_on_top",self.always_on_top.to_string()), ("auto_resize_window",self.auto_resize_window.to_string()), ("fit_to_window",self.fit_to_window.to_string()),
            ("zoom_percent",self.zoom_percent.to_string()), ("aspect_mode_index",self.aspect_mode.to_string()), ("rotation_quarters",self.rotation.to_string()),
            ("folder_icon_size_index",self.folder_icon_size.to_string()), ("ai_upscale_enabled",self.ai_enabled.to_string()),
            ("ai_difference_mode",self.difference_mode.to_string()), ("difference_threshold",self.difference_threshold.to_string()),
            ("difference_strategy",self.difference_strategy.to_string()),
            ("difference_padding",self.difference_padding.to_string()), ("ai_engine_index",self.ai_engine.to_string()), ("ai_model_index",self.ai_model.to_string()),
            ("denoise_mode_index",self.denoise_mode.to_string()), ("upscale_mode_index",self.upscale_mode.to_string()), ("fixed_count",self.fixed_count.to_string()),
            ("target_manual",self.target_manual.to_string()), ("target_width",self.target_width.to_string()), ("target_height",self.target_height.to_string()),
            ("prefetch_all",self.prefetch_all.to_string()), ("prefetch_depth",self.prefetch_depth.to_string()), ("skip_low_res_ai",self.skip_low_res_ai.to_string()),
            ("skip_low_res_display",self.skip_low_res_display.to_string()), ("skip_low_res_threshold",self.skip_low_res_threshold.to_string()),
            ("batch_processing",self.batch_processing.to_string()), ("gpu_index",self.gpu.to_string()),
            ("gpu_cache_limit_mib",self.gpu_cache_limit_mib.to_string()),
        ];
        let mut json = String::from("{\n");
        for (i,(key,value)) in fields.iter().enumerate() { if i>0 { json.push_str(",\n"); } json.push_str(&format!("  \"{key}\": {value}")); }
        for (i,value) in self.keybinds.iter().enumerate() { json.push_str(&format!(",\n  \"keybind_{i}\": \"{}\"", escape(value))); }
        json.push_str("\n}\n");
        let _ = fs::write(path(), json);
    }
}

fn path() -> PathBuf { std::env::current_exe().ok().and_then(|p| p.parent().map(|b| b.join("settings.json"))).unwrap_or_else(|| "settings.json".into()) }
fn field<'a>(text:&'a str,key:&str)->Option<&'a str>{Some(text.split_once(&format!("\"{key}\""))?.1.split_once(':')?.1.trim_start().split([',','\n','}']).next()?.trim())}
fn boolean(text:&str,key:&str)->Option<bool>{match field(text,key)?{"true"=>Some(true),"false"=>Some(false),_=>None}}
fn number<T:std::str::FromStr>(text:&str,key:&str)->Option<T>{field(text,key)?.trim_matches('"').parse().ok()}
fn string(text:&str,key:&str)->Option<String>{
    let rest=text.split_once(&format!("\"{key}\""))?.1.split_once(':')?.1.trim_start();
    let mut chars=rest.strip_prefix('"')?.chars();
    let mut result=String::new();
    let mut escaped=false;
    while let Some(ch)=chars.next(){
        if escaped { result.push(match ch {'n'=>'\n','r'=>'\r','t'=>'\t',other=>other}); escaped=false; }
        else if ch=='\\' { escaped=true; }
        else if ch=='"' { return Some(result); }
        else { result.push(ch); }
    }
    None
}
fn escape(value:&str)->String{value.replace('\\',"\\\\").replace('"',"\\\"")}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reads_punctuation_and_escaped_keybinds() {
        let text=r#"{"keybind_0": ",", "keybind_1": "Ctrl+\""}"#;
        assert_eq!(string(text,"keybind_0").as_deref(),Some(","));
        assert_eq!(string(text,"keybind_1").as_deref(),Some("Ctrl+\""));
    }
}
