// Copyright (c), W.Dee and contributors All rights reserved.
// Derived from Kirikiri Z TLG6 and tlg_rs. See LICENSES/KIRIKIRI-Z.txt and LICENSES/TLG_RS-UNLICENSE.txt.
// This Rust adaptation has been modified for integration into aXv.
use scroll::Pread;

#[repr(C)]
#[derive(Pread, Debug)]
pub(crate) struct TLG6Header {
    pub(crate) colors: u8,
    data_flag: u8,
    color_type: u8,
    external_golomb_table: u8,
    pub(crate) image_width: u32,
    pub(crate) image_height: u32,
    max_bit_length: u32,
}
