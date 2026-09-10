use anyhow::{bail, Context, Result};
use image::RgbaImage;
use libloading::{Library, Symbol};
use std::path::{Path, PathBuf};

type DecodeFn = unsafe extern "C" fn(
    *const u8, usize, usize, u32, u32, u32, *mut u8, usize,
) -> i32;

pub fn decode(data: &[u8]) -> Result<RgbaImage> {
    let (version, width, height, colors, offset) = header(data)?;
    if version != 6 { bail!("TLG5のRustデコーダはまだ利用できません"); }
    let dll = find_dll().context("tlg6_native.dllが見つかりません")?;
    let mut bgra = vec![0_u8; width as usize * height as usize * 4];
    unsafe {
        let library = Library::new(&dll).with_context(|| format!("{}を読み込めません", dll.display()))?;
        let function: Symbol<DecodeFn> = library.get(b"tlg6_decode\0").context("tlg6_decodeが見つかりません")?;
        let code = function(data.as_ptr(), data.len(), offset, width, height, colors, bgra.as_mut_ptr(), bgra.len());
        if code != 0 { bail!("TLG6デコードに失敗しました（コード{code}）"); }
    }
    for pixel in bgra.chunks_exact_mut(4) { pixel.swap(0, 2); }
    RgbaImage::from_raw(width, height, bgra).context("TLG6出力サイズが不正です")
}

fn header(data: &[u8]) -> Result<(u8, u32, u32, u32, usize)> {
    if data.len() < 38 { bail!("TLGヘッダーが短すぎます"); }
    let mut base = 0;
    if data.starts_with(b"TLG0.0\0sds\x1a") { base = 15; }
    let version = if data.get(base..base + 11) == Some(b"TLG6.0\0raw\x1a") { 6 }
        else if data.get(base..base + 11) == Some(b"TLG5.0\0raw\x1a") { 5 }
        else { bail!("TLG5/TLG6署名がありません"); };
    let colors = data[base + 11] as u32;
    let pos = if version == 6 { base + 15 } else { base + 12 };
    let width = u32::from_le_bytes(data[pos..pos + 4].try_into()?);
    let height = u32::from_le_bytes(data[pos + 4..pos + 8].try_into()?);
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > 64_000_000 { bail!("TLG画像サイズが不正です"); }
    Ok((version, width, height, colors, pos + 8))
}

fn find_dll() -> Option<PathBuf> {
    let exe_dir = std::env::current_exe().ok().and_then(|path| path.parent().map(Path::to_path_buf));
    let current = std::env::current_dir().ok();
    exe_dir.into_iter().chain(current).flat_map(|base| [base.join("tlg6_native.dll"), base.join("ai_upscale/tlg6_native.dll")]).find(|path| path.is_file())
}

