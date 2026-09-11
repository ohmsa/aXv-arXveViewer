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

/// 書庫切替前の非同期結果や、存在しないページ番号を受理しない。
pub fn accepts_result(active_generation: u64, result_generation: u64, index: usize, page_count: usize) -> bool {
    active_generation == result_generation && index < page_count
}

pub fn prioritize_by_similarity(mut pages: Vec<(usize, u64, usize)>, current: usize) -> Vec<usize> {
    pages.sort_by_key(|(index, score, original_position)| ((*index != current), *score, *original_position));
    pages.into_iter().map(|(index, _, _)| index).collect()
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

    #[test]
    fn archive_switch_rejects_stale_or_out_of_range_ai_results() {
        assert!(accepts_result(8, 8, 2, 3));
        assert!(!accepts_result(9, 8, 2, 3));
        assert!(!accepts_result(8, 8, 3, 3));
    }

    #[test]
    fn similarity_changes_processing_order_without_changing_page_ids() {
        let order = prioritize_by_similarity(vec![(3, 90, 0), (4, 10, 1), (5, 30, 2)], 3);
        assert_eq!(order, [3, 4, 5]);
        let shuffled = prioritize_by_similarity(vec![(3, 90, 0), (4, 30, 1), (5, 10, 2)], 99);
        assert_eq!(shuffled, [5, 4, 3]);
    }
}
