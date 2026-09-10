use anyhow::{bail, Context, Result};
use image::RgbaImage;
use std::{fs, path::{Path, PathBuf}, process::{Command, Stdio}, sync::{atomic::{AtomicU64, Ordering}, mpsc::{self, Receiver}, Arc}, thread, time::{Duration, SystemTime, UNIX_EPOCH}};

#[derive(Clone)]
pub struct AiSettings {
    pub enabled: bool,
    pub difference_mode: bool,
    pub difference_threshold: u8,
    pub difference_padding: u32,
    pub engine: usize,
    pub model: usize,
    pub denoise_mode: usize,
    pub upscale_mode: usize,
    pub fixed_count: u32,
    pub target_manual: bool,
    pub target_width: u32,
    pub target_height: u32,
    pub prefetch_all: bool,
    pub prefetch_depth: u32,
    pub skip_low_res: bool,
    pub skip_low_res_threshold: u32,
    pub batch_processing: bool,
    pub gpu: usize,
}

pub struct AiResult {
    pub generation: u64,
    pub index: usize,
    pub result: std::result::Result<RgbaImage, String>,
}

pub fn start_upscale(
    generation: u64,
    index: usize,
    image: Arc<RgbaImage>,
    settings: AiSettings,
    composition: Option<(RgbaImage, crate::difference::DifferenceRect)>,
    active_generation: Arc<AtomicU64>,
) -> Receiver<AiResult> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let scale = engine_spec(&settings).map(|spec| spec.scale).unwrap_or(2);
        let result = run_ncnn(image.as_ref(), &settings, generation, &active_generation).map(|processed| {
            if let Some((mut base, rect)) = composition {
                image::imageops::replace(&mut base, &processed, i64::from(rect.x * scale), i64::from(rect.y * scale));
                base
            } else { processed }
        }).map_err(|error| error.to_string());
        let _ = sender.send(AiResult { generation, index, result });
    });
    receiver
}

struct EngineSpec { exe: &'static str, model_dir: &'static str, model: &'static str, scale: u32 }

fn engine_spec(settings: &AiSettings) -> Result<EngineSpec> {
    match settings.engine {
        0 => Ok(EngineSpec { exe: "realesrgan-ncnn-vulkan.exe", model_dir: "models", model: if settings.model == 1 { "realesrgan-x4plus" } else { "realesrgan-x4plus-anime" }, scale: 4 }),
        1 => {
            let models = ["up2x-no-denoise", "up3x-no-denoise", "up4x-no-denoise"];
            let scales = [2, 3, 4];
            let index = settings.model.min(models.len() - 1);
            Ok(EngineSpec { exe: "realcugan-ncnn-vulkan.exe", model_dir: "models-se", model: models[index], scale: scales[index] })
        }
        2 => Ok(EngineSpec { exe: "waifu2x-ncnn-vulkan.exe", model_dir: "models-cunet", model: if settings.model == 1 { "noise3_model" } else { "scale2.0x_model" }, scale: if settings.model == 1 { 1 } else { 2 } }),
        _ => bail!("OpenVINOバックエンドはこのビルドでは利用できません"),
    }
}

fn run_ncnn(image: &RgbaImage, settings: &AiSettings, generation: u64, active_generation: &AtomicU64) -> Result<RgbaImage> {
    let ai_dir = find_ai_dir().context("ai_upscaleフォルダが見つかりません")?;
    let spec = engine_spec(settings)?;
    let exe = ai_dir.join(spec.exe);
    if !exe.is_file() { bail!("{}が見つかりません", exe.display()); }
    let stamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos();
    let temp = std::env::temp_dir().join(format!("axv-ai-{}-{stamp}", std::process::id()));
    fs::create_dir_all(&temp)?;
    let input = temp.join("input.png");
    let output = temp.join("output.png");
    let result = (|| {
        image.save(&input)?;
        let mut command = Command::new(&exe);
        command.current_dir(&ai_dir).args(["-i"]).arg(&input).args(["-o"]).arg(&output);
        match settings.engine {
            0 => { command.args(["-n", spec.model, "-s", &spec.scale.to_string(), "-m", spec.model_dir]); }
            1 => { command.args(["-s", &spec.scale.to_string(), "-n", if settings.denoise_mode == 2 { "0" } else { "3" }, "-m", spec.model_dir]); }
            2 => {
                let noise = if settings.model == 1 { "3" } else if settings.denoise_mode == 2 { "-1" } else { "3" };
                command.args(["-s", &spec.scale.to_string(), "-n", noise, "-m", spec.model_dir]);
            }
            _ => unreachable!(),
        }
        command.stdout(Stdio::null()).stderr(Stdio::piped());
        if settings.gpu > 0 { command.args(["-g", &(settings.gpu - 1).to_string()]); }
        let mut process = command.spawn().context("RealCUGANを起動できません")?;
        loop {
            if active_generation.load(Ordering::Acquire) != generation {
                let _ = process.kill();
                let _ = process.wait();
                bail!("AI処理をキャンセルしました");
            }
            if process.try_wait()?.is_some() { break; }
            thread::sleep(Duration::from_millis(20));
        }
        let process = process.wait_with_output()?;
        if !process.status.success() {
            bail!("RealCUGAN失敗: {}", String::from_utf8_lossy(&process.stderr));
        }
        Ok(image::open(&output).context("RealCUGAN出力を読み込めません")?.to_rgba8())
    })();
    let _ = fs::remove_dir_all(&temp);
    result
}

pub fn find_ai_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok().and_then(|path| path.parent().map(Path::to_path_buf));
    let cwd = std::env::current_dir().ok();
    exe.into_iter().chain(cwd).flat_map(|base| [base.join("ai_upscale"), base.parent().unwrap_or(&base).join("ai_upscale")]).find(|path| path.is_dir())
}

impl Default for AiSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            difference_mode: false,
            difference_threshold: 0,
            difference_padding: 48,
            engine: 1,
            model: 0,
            denoise_mode: 0,
            upscale_mode: 0,
            fixed_count: 1,
            target_manual: false,
            target_width: 1920,
            target_height: 1080,
            prefetch_all: true,
            prefetch_depth: 5,
            skip_low_res: true,
            skip_low_res_threshold: 300,
            batch_processing: true,
            gpu: 0,
        }
    }
}

// The external RealCUGAN adapter will live here. Its PNG boundary is deliberately
// kept out of the image library so a future in-process ncnn backend can consume
// RGBA buffers without changing archive and cache management.
