// Copyright (c), W.Dee and contributors All rights reserved.
// Derived from Kirikiri Z TLG6 and tlg_rs. See LICENSES/KIRIKIRI-Z.txt and LICENSES/TLG_RS-UNLICENSE.txt.
// This Rust adaptation has been modified for integration into aXv.
#[inline]
pub(crate) fn transform(r: &mut u8, g: &mut u8, b: &mut u8, index: u8) {
    match index {
        0 => {}
        1 => {
            *r = r.wrapping_add(*g);
            *b = b.wrapping_add(*g);
        }
        2 => {
            *g = g.wrapping_add(*b);
            *r = r.wrapping_add(*g);
        }
        3 => {
            *g = g.wrapping_add(*r);
            *b = b.wrapping_add(*g);
        }
        4 => {
            *b = b.wrapping_add(*r);
            *g = g.wrapping_add(*b);
            *r = r.wrapping_add(*g);
        }
        5 => {
            *b = b.wrapping_add(*r);
            *g = g.wrapping_add(*b);
        }
        6 => {
            *b = b.wrapping_add(*g);
        }
        7 => {
            *g = g.wrapping_add(*b);
        }
        8 => {
            *r = r.wrapping_add(*g);
        }
        9 => {
            *r = r.wrapping_add(*b);
            *g = g.wrapping_add(*r);
            *b = b.wrapping_add(*g);
        }
        10 => {
            *b = b.wrapping_add(*r);
            *g = g.wrapping_add(*r);
        }
        11 => {
            *r = r.wrapping_add(*b);
            *g = g.wrapping_add(*b);
        }
        12 => {
            *r = r.wrapping_add(*b);
            *g = g.wrapping_add(*r);
        }
        13 => {
            *b = b.wrapping_add(*g);
            *r = r.wrapping_add(*b);
            *g = g.wrapping_add(*r);
        }
        14 => {
            *g = g.wrapping_add(*r);
            *b = b.wrapping_add(*g);
            *r = r.wrapping_add(*b);
        }
        15 => {
            *g = g.wrapping_add(*b << 1);
            *r = r.wrapping_add(*b << 1);
        }
        _ => debug_assert!(false, "invalid TLG6 transform index"),
    }
}
