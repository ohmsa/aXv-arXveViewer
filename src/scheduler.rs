/// Python版と同じ規則でAI処理候補を並べる。
/// 現在ページから末尾までを表示順、その後に通過済みページを近い順に置く。
pub fn processing_order(page_count: usize, current: usize) -> Vec<usize> {
    if page_count == 0 {
        return Vec::new();
    }
    let current = current.min(page_count - 1);
    (current..page_count)
        .chain((0..current).rev())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn current_and_future_pages_are_processed_in_display_order() {
        assert_eq!(processing_order(7, 3), vec![3, 4, 5, 6, 2, 1, 0]);
    }

    #[test]
    fn empty_and_out_of_range_inputs_are_safe() {
        assert!(processing_order(0, 99).is_empty());
        assert_eq!(processing_order(3, 99), vec![2, 1, 0]);
    }
}

