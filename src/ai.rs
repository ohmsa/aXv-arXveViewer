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
    pub skip_low_res_ai: bool,
    pub skip_low_res_display: bool,
    pub skip_low_res_threshold: u32,
    pub batch_processing: bool,
    pub gpu: usize,
}

pub struct AiResult {
    pub generation: u64,
    pub index: usize,
    pub elapsed: Duration,
    pub result: std::result::Result<RgbaImage, String>,
}

pub fn start_upscale(
    generation: u64,
    index: usize,
    image: Arc<RgbaImage>,
    settings: AiSettings,
    composition: Option<(RgbaImage, Vec<(RgbaImage, crate::difference::DifferenceRect)>)>,
    active_generation: Arc<AtomicU64>,
) -> Receiver<AiResult> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let started = std::time::Instant::now();
        let scale = engine_spec(&settings).map(|spec| spec.scale).unwrap_or(2);
        let result = (|| -> Result<RgbaImage> {
            if let Some((mut base, regions)) = composition {
                let processed_regions = process_patch_atlas(regions, &settings, scale, generation, &active_generation)?;
                for (processed, rect) in processed_regions {
                    feather_patch(&mut base, &processed, rect.x * scale, rect.y * scale, 16);
                }
                Ok(base)
            } else {
                run_ncnn(image.as_ref(), &settings, generation, &active_generation)
            }
        })().map_err(|error| error.to_string());
        let _ = sender.send(AiResult { generation, index, elapsed: started.elapsed(), result });
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
        4 => Ok(EngineSpec { exe: "realsr-ncnn-vulkan.exe", model_dir: "models-DF2K", model: "", scale: 4 }),
        5 => Ok(EngineSpec { exe: "srmd-ncnn-vulkan.exe", model_dir: "models-srmd", model: "", scale: 2 }),
        6 => Ok(EngineSpec { exe: "realcugan-ncnn-vulkan.exe", model_dir: "models-se", model: "up2x-no-denoise", scale: 4 }),
        _ => bail!("このAIバックエンドは現在利用できません"),
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
    let mut input = temp.join("input.png");
    let output = temp.join("output.png");
    let result = (|| {
        image.save(&input)?;
        if settings.engine == 6 {
            let preview = temp.join("anime4k.png");
            let anime4k = ai_dir.join("ac_cli.exe");
            let mut preview_command = Command::new(&anime4k);
            preview_command.current_dir(&ai_dir).args(["-i"]).arg(&input).args(["-o"]).arg(&preview)
                .args(["-p", "opencl", "-f", "2"])
                .stdout(Stdio::null()).stderr(Stdio::piped());
            let preview_result = wait_for_command(preview_command, generation, active_generation)?;
            if !preview_result.status.success() {
                bail!("Anime4K失敗: {}", String::from_utf8_lossy(&preview_result.stderr));
            }
            input = preview;
        }
        let mut command = Command::new(&exe);
        command.current_dir(&ai_dir).args(["-i"]).arg(&input).args(["-o"]).arg(&output);
        match settings.engine {
            0 => { command.args(["-n", spec.model, "-s", &spec.scale.to_string(), "-m", spec.model_dir]); }
            1 => { command.args(["-s", &spec.scale.to_string(), "-n", if settings.denoise_mode == 2 { "0" } else { "3" }, "-m", spec.model_dir]); }
            2 => {
                let noise = if settings.model == 1 { "3" } else if settings.denoise_mode == 2 { "-1" } else { "3" };
                command.args(["-s", &spec.scale.to_string(), "-n", noise, "-m", spec.model_dir]);
            }
            4 => { command.args(["-s", "4", "-m", spec.model_dir]); }
            5 => {
                let noise = if settings.denoise_mode == 2 { "-1" } else { "3" };
                command.args(["-s", "2", "-n", noise, "-m", spec.model_dir]);
            }
            6 => { command.args(["-s", "2", "-n", "0", "-m", spec.model_dir]); }
            _ => unreachable!(),
        }
        command.stdout(Stdio::null()).stderr(Stdio::piped());
        if settings.gpu > 0 { command.args(["-g", &(settings.gpu - 1).to_string()]); }
        let process = wait_for_command(command, generation, active_generation)
            .context("AIエンジンを起動できません")?;
        if !process.status.success() {
            bail!("RealCUGAN失敗: {}", String::from_utf8_lossy(&process.stderr));
        }
        let mut result = image::open(&output).context("AI出力を読み込めません")?.to_rgba8();
        remove_isolated_color_spikes(&mut result);
        Ok(result)
    })();
    let _ = fs::remove_dir_all(&temp);
    result
}

pub fn find_ai_dir() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok().and_then(|path| path.parent().map(Path::to_path_buf));
    let cwd = std::env::current_dir().ok();
    exe.into_iter().chain(cwd).flat_map(|base| [base.join("ai_upscale"), base.parent().unwrap_or(&base).join("ai_upscale")]).find(|path| path.is_dir())
}

fn process_patch_atlas(
    regions: Vec<(RgbaImage, crate::difference::DifferenceRect)>, settings: &AiSettings,
    scale: u32, generation: u64, active_generation: &AtomicU64,
) -> Result<Vec<(RgbaImage, crate::difference::DifferenceRect)>> {
    if regions.len() <= 1 { return regions.into_iter().map(|(crop, rect)| run_ncnn(&crop, settings, generation, active_generation).map(|image| (image, rect))).collect(); }
    const GUARD: u32 = 16;
    const MAX_OUTPUT_PIXELS: u64 = 32_000_000;
    let columns = (regions.len() as f64).sqrt().ceil() as u32;
    let rows = (regions.len() as u32).div_ceil(columns);
    let cell_width = regions.iter().map(|(image, _)| image.width()).max().unwrap_or(1) + GUARD * 2;
    let cell_height = regions.iter().map(|(image, _)| image.height()).max().unwrap_or(1) + GUARD * 2;
    let atlas_width = cell_width * columns;
    let atlas_height = cell_height * rows;
    if u64::from(atlas_width) * u64::from(atlas_height) * u64::from(scale) * u64::from(scale) > MAX_OUTPUT_PIXELS {
        return regions.into_iter().map(|(crop, rect)| run_ncnn(&crop, settings, generation, active_generation).map(|image| (image, rect))).collect();
    }
    let mut atlas = RgbaImage::new(atlas_width, atlas_height);
    let mut placements = Vec::with_capacity(regions.len());
    for (index, (crop, rect)) in regions.into_iter().enumerate() {
        let origin_x = index as u32 % columns * cell_width;
        let origin_y = index as u32 / columns * cell_height;
        for y in 0..crop.height() + GUARD * 2 {
            for x in 0..crop.width() + GUARD * 2 {
                let source_x = x.saturating_sub(GUARD).min(crop.width() - 1);
                let source_y = y.saturating_sub(GUARD).min(crop.height() - 1);
                atlas.put_pixel(origin_x + x, origin_y + y, *crop.get_pixel(source_x, source_y));
            }
        }
        placements.push((origin_x + GUARD, origin_y + GUARD, crop.width(), crop.height(), rect));
    }
    let processed_atlas = run_ncnn(&atlas, settings, generation, active_generation)?;
    Ok(placements.into_iter().map(|(x, y, width, height, rect)| {
        let crop = image::imageops::crop_imm(&processed_atlas, x * scale, y * scale, width * scale, height * scale).to_image();
        (crop, rect)
    }).collect())
}

/// Removes only strongly-colored one-pixel outliers surrounded by four mutually
/// similar pixels. White highlights, line ends and textured areas are retained.
fn remove_isolated_color_spikes(image: &mut RgbaImage) {
    let (width, height) = image.dimensions();
    if width < 3 || height < 3 { return; }
    let source = image.clone();
    for y in 1..height - 1 {
        for x in 1..width - 1 {
            let center = source.get_pixel(x, y).0;
            let chroma = center[..3].iter().max().unwrap() - center[..3].iter().min().unwrap();
            if chroma < 80 { continue; }
            let neighbors = [
                source.get_pixel(x - 1, y).0, source.get_pixel(x + 1, y).0,
                source.get_pixel(x, y - 1).0, source.get_pixel(x, y + 1).0,
            ];
            let mut average = [0_u8; 3];
            let mut smooth = true;
            for channel in 0..3 {
                let min = neighbors.iter().map(|pixel| pixel[channel]).min().unwrap();
                let max = neighbors.iter().map(|pixel| pixel[channel]).max().unwrap();
                if max - min > 24 { smooth = false; break; }
                average[channel] = (neighbors.iter().map(|pixel| u16::from(pixel[channel])).sum::<u16>() / 4) as u8;
            }
            if !smooth { continue; }
            let distance = (0..3).map(|channel| center[channel].abs_diff(average[channel])).max().unwrap();
            if distance > 96 {
                image.put_pixel(x, y, image::Rgba([average[0], average[1], average[2], center[3]]));
            }
        }
    }
}

fn feather_patch(base: &mut RgbaImage, patch: &RgbaImage, dest_x: u32, dest_y: u32, feather: u32) {
    let width = patch.width().min(base.width().saturating_sub(dest_x));
    let height = patch.height().min(base.height().saturating_sub(dest_y));
    for y in 0..height {
        for x in 0..width {
            let mut weight = 1.0_f32;
            if dest_x > 0 { weight = weight.min(x as f32 / feather as f32); }
            if dest_y > 0 { weight = weight.min(y as f32 / feather as f32); }
            if dest_x + width < base.width() { weight = weight.min((width - 1 - x) as f32 / feather as f32); }
            if dest_y + height < base.height() { weight = weight.min((height - 1 - y) as f32 / feather as f32); }
            let weight = weight.clamp(0.0, 1.0);
            let old = base.get_pixel(dest_x + x, dest_y + y).0;
            let new = patch.get_pixel(x, y).0;
            let mut blended = [0_u8; 4];
            for channel in 0..4 {
                blended[channel] = (old[channel] as f32 * (1.0 - weight) + new[channel] as f32 * weight).round() as u8;
            }
            base.put_pixel(dest_x + x, dest_y + y, image::Rgba(blended));
        }
    }
}

fn wait_for_command(mut command: Command, generation: u64, active_generation: &AtomicU64) -> Result<std::process::Output> {
    let mut process = command.spawn()?;
    loop {
        if active_generation.load(Ordering::Acquire) != generation {
            let _ = process.kill();
            let _ = process.wait();
            bail!("AI処理をキャンセルしました");
        }
        if process.try_wait()?.is_some() { break; }
        thread::sleep(Duration::from_millis(20));
    }
    Ok(process.wait_with_output()?)
}

pub fn available_engines() -> Vec<(usize, &'static str)> {
    let Some(dir) = find_ai_dir() else { return Vec::new() };
    [
        (6, "Anime4K → Real-CUGAN", "ac_cli.exe"),
        (0, "Real-ESRGAN ncnn Vulkan", "realesrgan-ncnn-vulkan.exe"),
        (1, "Real-CUGAN ncnn Vulkan", "realcugan-ncnn-vulkan.exe"),
        (2, "waifu2x ncnn Vulkan", "waifu2x-ncnn-vulkan.exe"),
        (4, "RealSR ncnn Vulkan", "realsr-ncnn-vulkan.exe"),
        (5, "SRMD ncnn Vulkan", "srmd-ncnn-vulkan.exe"),
    ].into_iter().filter(|(id, _, exe)| dir.join(exe).is_file() && (*id != 6 || dir.join("realcugan-ncnn-vulkan.exe").is_file()))
        .map(|(id, name, _)| (id, name)).collect()
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
            skip_low_res_ai: true,
            skip_low_res_display: false,
            skip_low_res_threshold: 300,
            batch_processing: true,
            gpu: 0,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn removes_isolated_colored_spike_but_keeps_white_highlight() {
        let mut image = RgbaImage::from_pixel(5, 5, image::Rgba([50, 50, 80, 255]));
        image.put_pixel(2, 2, image::Rgba([240, 0, 20, 255]));
        remove_isolated_color_spikes(&mut image);
        assert_eq!(image.get_pixel(2, 2).0, [50, 50, 80, 255]);

        image.put_pixel(2, 2, image::Rgba([240, 240, 240, 255]));
        remove_isolated_color_spikes(&mut image);
        assert_eq!(image.get_pixel(2, 2).0, [240, 240, 240, 255]);
    }
}

// The external RealCUGAN adapter will live here. Its PNG boundary is deliberately
// kept out of the image library so a future in-process ncnn backend can consume
// RGBA buffers without changing archive and cache management.
