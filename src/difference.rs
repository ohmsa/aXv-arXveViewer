use image::RgbaImage;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct DifferenceRect {
    pub x: u32,
    pub y: u32,
    pub width: u32,
    pub height: u32,
}

pub fn find_difference(previous: &RgbaImage, current: &RgbaImage, threshold: u8, padding: u32) -> Option<DifferenceRect> {
    if previous.dimensions() != current.dimensions() {
        return Some(DifferenceRect { x: 0, y: 0, width: current.width(), height: current.height() });
    }

    let (width, height) = current.dimensions();
    let (mut min_x, mut min_y) = (width, height);
    let (mut max_x, mut max_y) = (0, 0);
    let mut changed = false;
    for y in 0..height {
        for x in 0..width {
            let a = previous.get_pixel(x, y).0;
            let b = current.get_pixel(x, y).0;
            if a.iter().zip(b).any(|(left, right)| left.abs_diff(right) > threshold) {
                changed = true;
                min_x = min_x.min(x);
                min_y = min_y.min(y);
                max_x = max_x.max(x);
                max_y = max_y.max(y);
            }
        }
    }
    changed.then(|| {
        let x = min_x.saturating_sub(padding);
        let y = min_y.saturating_sub(padding);
        let right = (max_x + 1).saturating_add(padding).min(width);
        let bottom = (max_y + 1).saturating_add(padding).min(height);
        DifferenceRect { x, y, width: right - x, height: bottom - y }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::Rgba;

    #[test]
    fn detects_and_pads_changed_area() {
        let base = RgbaImage::new(20, 20);
        let mut changed = base.clone();
        changed.put_pixel(10, 8, Rgba([255, 0, 0, 255]));
        assert_eq!(find_difference(&base, &changed, 0, 2), Some(DifferenceRect { x: 8, y: 6, width: 5, height: 5 }));
    }
}
