use anyhow::{bail, Context, Result};
use image::RgbaImage;
use std::{
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
};

const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "bmp", "gif", "tlg", "tlg5", "tlg6"];

pub struct Page {
    pub name: String,
    encoded: Arc<[u8]>,
    pub decoded: Option<Arc<RgbaImage>>,
}

impl Page {
    fn new(name: String, encoded: Vec<u8>) -> Self {
        Self { name, encoded: encoded.into(), decoded: None }
    }

    pub fn ensure_decoded(&mut self) -> Result<Arc<RgbaImage>> {
        if let Some(image) = &self.decoded {
            return Ok(image.clone());
        }
        let image = if matches!(extension(Path::new(&self.name)).as_str(), "tlg" | "tlg5" | "tlg6") {
            crate::tlg::decode(&self.encoded)?
        } else {
            image::load_from_memory(&self.encoded)
                .with_context(|| format!("{} をデコードできません", self.name))?
                .to_rgba8()
        };
        let image = Arc::new(image);
        self.decoded = Some(image.clone());
        Ok(image)
    }

    pub fn decode_original(&self) -> Result<RgbaImage> {
        if matches!(extension(Path::new(&self.name)).as_str(), "tlg" | "tlg5" | "tlg6") {
            crate::tlg::decode(&self.encoded)
        } else {
            Ok(image::load_from_memory(&self.encoded)
                .with_context(|| format!("{} をデコードできません", self.name))?.to_rgba8())
        }
    }

    pub fn decoded_bytes(&self) -> usize {
        self.decoded.as_ref().map_or(0, |image| image.as_raw().len())
    }

    pub fn encoded_bytes(&self) -> usize { self.encoded.len() }

    pub fn dimensions(&self) -> Option<(u32, u32)> {
        self.decoded.as_ref().map(|image| image.dimensions())
    }

    pub fn replace_decoded(&mut self, image: RgbaImage) {
        self.decoded = Some(Arc::new(image));
    }
}

fn sort_pages(pages: &mut [Page], natural_sort: bool) {
    // AIキューはこの配列のindexをページIDとして使う。デコード後やキャッシュ
    // 交換後に再ソートせず、表示順と処理順が途中で食い違わないようにする。
    pages.sort_by(|left, right| {
        if natural_sort {
            natord::compare_ignore_case(&left.name, &right.name).then_with(|| left.name.cmp(&right.name))
        } else {
            left.name.cmp(&right.name)
        }
    });
}

pub struct ImageLibrary {
    pub source: PathBuf,
    pub pages: Vec<Page>,
    pub generation: u64,
}

impl ImageLibrary {
    pub fn open(path: &Path, generation: u64, natural_sort: bool) -> Result<Self> {
        let mut pages = if path.is_dir() {
            load_folder(path)?
        } else {
            let detected = detect_kind(path)?;
            match detected.as_str() {
                "zip" | "cbz" => load_zip(path)?,
                "rar" | "cbr" => load_rar(path)?,
                "7z" => load_7z(path)?,
                ext if IMAGE_EXTENSIONS.contains(&ext) => {
                    vec![Page::new(file_name(path), fs::read(path)?)]
                }
                _ => bail!("未対応のファイルです: {}", path.display()),
            }
        };

        sort_pages(&mut pages, natural_sort);
        for page in &mut pages {
            page.ensure_decoded()?;
        }
        if pages.is_empty() {
            bail!("表示できる画像がありません");
        }
        Ok(Self { source: path.to_path_buf(), pages, generation })
    }

    pub fn purge_before_current_window(&mut self, current: usize, keep_previous: usize) -> usize {
        let boundary = current.saturating_sub(keep_previous);
        let mut freed = 0;
        for page in &mut self.pages[..boundary] {
            freed += page.decoded_bytes();
            page.decoded = None;
        }
        freed
    }

    pub fn decoded_bytes(&self) -> usize {
        self.pages.iter().map(Page::decoded_bytes).sum()
    }

    pub fn restore_originals(&mut self) -> Result<()> {
        for page in &mut self.pages {
            page.decoded = Some(Arc::new(page.decode_original()?));
        }
        Ok(())
    }
}

fn detect_kind(path: &Path) -> Result<String> {
    let ext = extension(path);
    if matches!(ext.as_str(), "zip" | "cbz" | "rar" | "cbr" | "7z") || IMAGE_EXTENSIONS.contains(&ext.as_str()) {
        return Ok(ext);
    }
    let mut file = fs::File::open(path)?;
    let mut magic = [0_u8; 8];
    let count = file.read(&mut magic)?;
    let bytes = &magic[..count];
    if bytes.starts_with(b"PK\x03\x04") || bytes.starts_with(b"PK\x05\x06") || bytes.starts_with(b"PK\x07\x08") { return Ok("zip".into()); }
    if bytes.starts_with(b"Rar!\x1a\x07") { return Ok("rar".into()); }
    if bytes.starts_with(b"7z\xbc\xaf\x27\x1c") { return Ok("7z".into()); }
    Ok(ext)
}

fn load_folder(path: &Path) -> Result<Vec<Page>> {
    let mut pages = Vec::new();
    for entry in fs::read_dir(path)? {
        let path = entry?.path();
        if path.is_file() && IMAGE_EXTENSIONS.contains(&extension(&path).as_str()) {
            pages.push(Page::new(file_name(&path), fs::read(&path)?));
        }
    }
    Ok(pages)
}

fn load_zip(path: &Path) -> Result<Vec<Page>> {
    let file = fs::File::open(path)?;
    let mut archive = zip::ZipArchive::new(file)?;
    let mut pages = Vec::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        if entry.is_dir() || !is_image_name(entry.name()) {
            continue;
        }
        let mut bytes = Vec::with_capacity(entry.size() as usize);
        entry.read_to_end(&mut bytes)?;
        pages.push(Page::new(entry.name().to_owned(), bytes));
    }
    Ok(pages)
}

fn load_rar(path: &Path) -> Result<Vec<Page>> {
    let listing = Command::new(unrar_path())
        .args(["lb", "-inul", "-scf"])
        .arg(path)
        .output()
        .context("unrar.exeを起動できません")?;
    if !listing.status.success() {
        bail!("RARの一覧取得に失敗しました");
    }

    let mut pages = Vec::new();
    for name in String::from_utf8_lossy(&listing.stdout).lines().filter(|name| is_image_name(name)) {
        let output = Command::new(unrar_path())
            .arg("p")
            .arg("-inul")
            .arg(path)
            .arg(name)
            .output()
            .with_context(|| format!("RAR内の{name}を取得できません"))?;
        if output.status.success() {
            pages.push(Page::new(name.to_owned(), output.stdout));
        }
    }
    Ok(pages)
}

fn load_7z(path: &Path) -> Result<Vec<Page>> {
    let tool = external_tool("7z.exe", "7z");
    let listing = Command::new(&tool).args(["l", "-slt", "-ba"]).arg(path).output().context("7z.exeを起動できません")?;
    if !listing.status.success() { bail!("7zの一覧取得に失敗しました"); }
    let names = String::from_utf8_lossy(&listing.stdout).lines()
        .filter_map(|line| line.strip_prefix("Path = "))
        .filter(|name| is_image_name(name)).map(str::to_owned).collect::<Vec<_>>();
    let mut pages = Vec::new();
    for name in names {
        let output = Command::new(&tool).args(["x", "-so"]).arg(path).arg(&name).output()?;
        if output.status.success() { pages.push(Page::new(name, output.stdout)); }
    }
    Ok(pages)
}

fn unrar_path() -> PathBuf {
    let bundled = std::env::current_exe().ok().and_then(|p| p.parent().map(|p| p.join("unrar.exe")));
    if let Some(path) = bundled.filter(|p| p.exists()) {
        return path;
    }
    PathBuf::from("unrar")
}

fn external_tool(bundled_name: &str, path_name: &str) -> PathBuf {
    let bundled = std::env::current_exe().ok().and_then(|path| path.parent().map(|base| base.join(bundled_name)));
    bundled.filter(|path| path.exists()).unwrap_or_else(|| PathBuf::from(path_name))
}

fn extension(path: &Path) -> String {
    path.extension().and_then(|value| value.to_str()).unwrap_or_default().to_ascii_lowercase()
}

fn file_name(path: &Path) -> String {
    path.file_name().and_then(|value| value.to_str()).unwrap_or("image").to_owned()
}

fn is_image_name(name: &str) -> bool {
    let path = Path::new(name);
    IMAGE_EXTENSIONS.contains(&extension(path).as_str())
}

#[allow(dead_code)]
fn decode_from_cursor(bytes: Arc<[u8]>) -> Result<RgbaImage> {
    Ok(image::load(Cursor::new(bytes), image::ImageFormat::Png)?.to_rgba8())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png(seed: u8) -> Vec<u8> {
        let image = RgbaImage::from_pixel(2, 2, image::Rgba([seed, 0, 0, 255]));
        let mut bytes = Cursor::new(Vec::new());
        image::DynamicImage::ImageRgba8(image)
            .write_to(&mut bytes, image::ImageFormat::Png)
            .unwrap();
        bytes.into_inner()
    }

    #[test]
    fn natural_order_is_fixed_before_decode() {
        let mut pages = vec![
            Page::new("page10.png".into(), png(10)),
            Page::new("page2.png".into(), png(2)),
            Page::new("page1.png".into(), png(1)),
        ];
        sort_pages(&mut pages, true);
        for page in &mut pages { page.ensure_decoded().unwrap(); }
        assert_eq!(pages.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(), ["page1.png", "page2.png", "page10.png"]);
        assert_eq!(pages[1].decoded.as_ref().unwrap().get_pixel(0, 0).0[0], 2);
    }

    #[test]
    fn purge_does_not_change_page_order_and_page_can_be_decoded_again() {
        let mut library = ImageLibrary {
            source: PathBuf::from("test"),
            pages: (0..8).map(|i| Page::new(format!("page{i}.png"), png(i))).collect(),
            generation: 1,
        };
        for page in &mut library.pages { page.ensure_decoded().unwrap(); }
        library.purge_before_current_window(7, 3);
        assert_eq!(library.pages.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(),
                   ["page0.png", "page1.png", "page2.png", "page3.png", "page4.png", "page5.png", "page6.png", "page7.png"]);
        assert!(library.pages[0].decoded.is_none());
        assert_eq!(library.pages[0].ensure_decoded().unwrap().get_pixel(0, 0).0[0], 0);
    }
}
