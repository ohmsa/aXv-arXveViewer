use anyhow::{bail, Context, Result};
use image::RgbaImage;
use std::{
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
};

const IMAGE_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp", "bmp", "gif"];

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
        let image = image::load_from_memory(&self.encoded)
            .with_context(|| format!("{} をデコードできません", self.name))?
            .to_rgba8();
        let image = Arc::new(image);
        self.decoded = Some(image.clone());
        Ok(image)
    }

    pub fn decoded_bytes(&self) -> usize {
        self.decoded.as_ref().map_or(0, |image| image.as_raw().len())
    }
}

pub struct ImageLibrary {
    pub source: PathBuf,
    pub pages: Vec<Page>,
    pub generation: u64,
}

impl ImageLibrary {
    pub fn open(path: &Path, generation: u64) -> Result<Self> {
        let mut pages = if path.is_dir() {
            load_folder(path)?
        } else {
            match extension(path).as_str() {
                "zip" => load_zip(path)?,
                "rar" => load_rar(path)?,
                ext if IMAGE_EXTENSIONS.contains(&ext) => {
                    vec![Page::new(file_name(path), fs::read(path)?)]
                }
                _ => bail!("未対応のファイルです: {}", path.display()),
            }
        };

        pages.sort_by(|left, right| natord::compare_ignore_case(&left.name, &right.name));
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
        .args(["lb", "-inul"])
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

fn unrar_path() -> PathBuf {
    let bundled = std::env::current_exe().ok().and_then(|p| p.parent().map(|p| p.join("unrar.exe")));
    if let Some(path) = bundled.filter(|p| p.exists()) {
        return path;
    }
    PathBuf::from("unrar")
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
