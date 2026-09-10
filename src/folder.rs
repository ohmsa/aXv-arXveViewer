use anyhow::{Context, Result};
use std::{fs, path::{Path, PathBuf}};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EntryKind { Folder, Image, Archive }

#[derive(Clone, Debug)]
pub struct FolderEntry {
    pub path: PathBuf,
    pub name: String,
    pub kind: EntryKind,
}

pub fn list(path: &Path, natural_sort: bool) -> Result<Vec<FolderEntry>> {
    let mut entries = Vec::new();
    for item in fs::read_dir(path).with_context(|| format!("{}を開けません", path.display()))? {
        let item = item?;
        let item_path = item.path();
        let kind = if item_path.is_dir() { Some(EntryKind::Folder) } else {
            match item_path.extension().and_then(|value| value.to_str()).unwrap_or_default().to_ascii_lowercase().as_str() {
                "png" | "jpg" | "jpeg" | "webp" | "bmp" | "gif" | "tlg" | "tlg5" | "tlg6" => Some(EntryKind::Image),
                "zip" | "rar" | "7z" | "cbz" | "cbr" => Some(EntryKind::Archive),
                _ => None,
            }
        };
        if let Some(kind) = kind {
            entries.push(FolderEntry { name: item.file_name().to_string_lossy().into_owned(), path: item_path, kind });
        }
    }
    entries.sort_by(|a, b| if natural_sort {
        natord::compare_ignore_case(&a.name, &b.name).then_with(|| a.name.cmp(&b.name))
    } else { a.name.cmp(&b.name) });
    Ok(entries)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filters_supported_items_and_keeps_natural_order() {
        let root = std::env::temp_dir().join(format!("axv-folder-test-{}", std::process::id()));
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(root.join("chapter2")).unwrap();
        fs::write(root.join("page10.png"), []).unwrap();
        fs::write(root.join("page2.jpg"), []).unwrap();
        fs::write(root.join("book.zip"), []).unwrap();
        fs::write(root.join("ignore.txt"), []).unwrap();
        let entries = list(&root, true).unwrap();
        assert_eq!(entries.iter().map(|entry| entry.name.as_str()).collect::<Vec<_>>(),
                   ["book.zip", "chapter2", "page2.jpg", "page10.png"]);
        assert_eq!(entries.iter().map(|entry| entry.kind).collect::<Vec<_>>(),
                   [EntryKind::Archive, EntryKind::Folder, EntryKind::Image, EntryKind::Image]);
        fs::remove_dir_all(root).unwrap();
    }
}
