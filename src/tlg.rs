// Copyright (c), W.Dee and contributors All rights reserved.
// TLG6 decoding is adapted from Kirikiri Z and tlg_rs. See LICENSES/.

use anyhow::{bail, Context, Result};
use image::RgbaImage;

const RAW_SIGNATURE_SIZE: usize = 11;
const TLG0_HEADER_SIZE: usize = 15;
const MAX_PIXELS: u64 = 64_000_000;

pub fn decode(data: &[u8]) -> Result<RgbaImage> {
    let raw = raw_stream(data)?;
    let (version, width, height, colors) = header(raw)?;
    if version != 6 { bail!("TLG5のRustデコーダはまだ利用できません"); }
    if !matches!(colors, 1 | 3 | 4) { bail!("TLG6の色数が不正です: {colors}"); }

    // The imported decoder is safe Rust, but older upstream code contains
    // indexing assertions. Convert those into a normal decode error so a
    // corrupt archive entry cannot terminate the viewer.
    let decoded = std::panic::catch_unwind(|| {
        crate::tlg_codec::formats::tlg6::Tlg6::from_bytes(raw)
            .and_then(|image| image.to_rgba_image())
    }).map_err(|_| anyhow::anyhow!("破損したTLG6データを検出しました"))??;

    if decoded.dimensions() != (width, height) {
        bail!("TLG6出力サイズがヘッダーと一致しません");
    }
    Ok(decoded)
}

fn raw_stream(data: &[u8]) -> Result<&[u8]> {
    if data.starts_with(b"TLG0.0\0sds\x1a") {
        return data.get(TLG0_HEADER_SIZE..).context("TLG0コンテナが短すぎます");
    }
    Ok(data)
}

fn header(raw: &[u8]) -> Result<(u8, u32, u32, u8)> {
    if raw.len() < 23 { bail!("TLGヘッダーが短すぎます"); }
    let version = if raw.get(..RAW_SIGNATURE_SIZE) == Some(b"TLG6.0\0raw\x1a") { 6 }
        else if raw.get(..RAW_SIGNATURE_SIZE) == Some(b"TLG5.0\0raw\x1a") { 5 }
        else { bail!("TLG5/TLG6署名がありません"); };
    let colors = raw[11];
    let position = if version == 6 {
        if raw.get(12..15) != Some(&[0, 0, 0]) { bail!("未対応のTLG6フラグです"); }
        15
    } else { 12 };
    let width = u32::from_le_bytes(raw.get(position..position + 4).context("TLG幅がありません")?.try_into()?);
    let height = u32::from_le_bytes(raw.get(position + 4..position + 8).context("TLG高さがありません")?.try_into()?);
    let pixels = u64::from(width) * u64::from(height);
    if width == 0 || height == 0 || pixels > MAX_PIXELS { bail!("TLG画像サイズが不正です"); }
    Ok((version, width, height, colors))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn header_bytes(signature: &[u8; 11], width: u32, height: u32) -> Vec<u8> {
        let mut data = signature.to_vec();
        data.extend_from_slice(&[4, 0, 0, 0]);
        data.extend_from_slice(&width.to_le_bytes());
        data.extend_from_slice(&height.to_le_bytes());
        data
    }

    #[test]
    fn rejects_short_and_unknown_headers() {
        assert!(decode(b"TLG6").is_err());
        assert!(decode(&[0; 32]).is_err());
    }

    #[test]
    fn rejects_invalid_dimensions_before_allocating() {
        let zero = header_bytes(b"TLG6.0\0raw\x1a", 0, 1);
        assert!(decode(&zero).is_err());
        let huge = header_bytes(b"TLG6.0\0raw\x1a", 100_000, 100_000);
        assert!(decode(&huge).is_err());
    }

    #[test]
    fn corrupt_payload_returns_error_instead_of_panicking() {
        let data = header_bytes(b"TLG6.0\0raw\x1a", 1, 1);
        let result = std::panic::catch_unwind(|| decode(&data));
        assert!(result.is_ok());
        assert!(result.unwrap().is_err());
    }
}
