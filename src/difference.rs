use image::RgbaImage;

/// Cheap first-pass distance used only to rank candidate base images. A full
/// block comparison is still performed after the best candidate is selected.
pub fn sampled_difference_score(left: &RgbaImage, right: &RgbaImage) -> u64 {
    if left.dimensions() != right.dimensions() { return u64::MAX; }
    let (width, height) = left.dimensions();
    let step = 8_usize;
    let mut score = 0_u64;
    for y in (0..height).step_by(step) {
        for x in (0..width).step_by(step) {
            let a = left.get_pixel(x, y).0;
            let b = right.get_pixel(x, y).0;
            score = score.saturating_add(a.iter().zip(b).map(|(x, y)| u64::from(x.abs_diff(y))).sum::<u64>());
        }
    }
    score
}

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

pub fn find_difference_regions(previous: &RgbaImage, current: &RgbaImage, threshold: u8, padding: u32) -> Vec<DifferenceRect> {
    if previous.dimensions() != current.dimensions() {
        return vec![DifferenceRect { x: 0, y: 0, width: current.width(), height: current.height() }];
    }
    const BLOCK_SIZE: u32 = 32;
    let (width, height) = current.dimensions();
    if width == 0 || height == 0 { return Vec::new(); }
    let columns = width.div_ceil(BLOCK_SIZE) as usize;
    let rows = height.div_ceil(BLOCK_SIZE) as usize;
    let mut changed = vec![false; columns * rows];
    for gy in 0..rows {
        let y0 = gy as u32 * BLOCK_SIZE;
        let y1 = (y0 + BLOCK_SIZE).min(height);
        for gx in 0..columns {
            let x0 = gx as u32 * BLOCK_SIZE;
            let x1 = (x0 + BLOCK_SIZE).min(width);
            'pixels: for y in y0..y1 {
                for x in x0..x1 {
                    let a = previous.get_pixel(x, y).0;
                    let b = current.get_pixel(x, y).0;
                    if a.iter().zip(b).any(|(left, right)| left.abs_diff(right) > threshold) {
                        changed[gy * columns + gx] = true;
                        break 'pixels;
                    }
                }
            }
        }
    }

    let mut visited = vec![false; columns * rows];
    let mut regions = Vec::new();
    for start_y in 0..rows {
        for start_x in 0..columns {
            let start = start_y * columns + start_x;
            if !changed[start] || visited[start] { continue; }
            let (mut min_x, mut max_x, mut min_y, mut max_y) = (start_x, start_x, start_y, start_y);
            let mut stack = vec![(start_x, start_y)];
            visited[start] = true;
            while let Some((x, y)) = stack.pop() {
                min_x = min_x.min(x); max_x = max_x.max(x);
                min_y = min_y.min(y); max_y = max_y.max(y);
                for (nx, ny) in [
                    (x.wrapping_sub(1), y), (x + 1, y), (x, y.wrapping_sub(1)), (x, y + 1),
                    (x.wrapping_sub(1), y.wrapping_sub(1)), (x + 1, y.wrapping_sub(1)),
                    (x.wrapping_sub(1), y + 1), (x + 1, y + 1),
                ] {
                    let next = ny.saturating_mul(columns).saturating_add(nx);
                    if nx < columns && ny < rows && changed[next] && !visited[next] {
                        visited[next] = true;
                        stack.push((nx, ny));
                    }
                }
            }
            let raw_x = min_x as u32 * BLOCK_SIZE;
            let raw_y = min_y as u32 * BLOCK_SIZE;
            let raw_right = ((max_x + 1) as u32 * BLOCK_SIZE).min(width);
            let raw_bottom = ((max_y + 1) as u32 * BLOCK_SIZE).min(height);
            let x = raw_x.saturating_sub(padding);
            let y = raw_y.saturating_sub(padding);
            let right = raw_right.saturating_add(padding).min(width);
            let bottom = raw_bottom.saturating_add(padding).min(height);
            regions.push(DifferenceRect { x, y, width: right - x, height: bottom - y });
        }
    }
    regions
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


    #[test]
    fn grid_keeps_separate_changes_as_separate_regions() {
        let base = RgbaImage::new(160, 160);
        let mut changed = base.clone();
        changed.put_pixel(5, 5, Rgba([255, 0, 0, 255]));
        changed.put_pixel(155, 155, Rgba([255, 0, 0, 255]));
        let regions = find_difference_regions(&base, &changed, 0, 0);
        assert_eq!(regions.len(), 2);
    }

    #[test]
    fn sampled_score_prefers_the_more_similar_image() {
        let base = RgbaImage::from_pixel(64, 64, Rgba([20, 30, 40, 255]));
        let mut close = base.clone();
        close.put_pixel(8, 8, Rgba([21, 30, 40, 255]));
        let far = RgbaImage::from_pixel(64, 64, Rgba([220, 10, 10, 255]));
        assert!(sampled_difference_score(&base, &close) < sampled_difference_score(&base, &far));
    }

}
