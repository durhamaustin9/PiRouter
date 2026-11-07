#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
openscad_bin="${OPENSCAD_BIN:-openscad}"
output_dir="${1:-$script_dir/stl-v3}"
source_file="$script_dir/pirouter_case_v3.scad"

if ! command -v "$openscad_bin" >/dev/null 2>&1; then
  echo "OpenSCAD was not found. Install it or set OPENSCAD_BIN." >&2
  exit 1
fi

mkdir -p "$output_dir"

# These generated files belong to the retired friction-only seam closure.
# Remove only the two known legacy exports so a refreshed v3 folder cannot
# accidentally send the wrong closure parts to the slicer.
rm -f "$output_dir/seam_clip.stl" "$output_dir/clip_fit_coupon.stl"

render_part() {
  local part_name="$1"
  local output_name="$2"
  echo "Exporting $output_name"
  "$openscad_bin" \
    --export-format binstl \
    -o "$output_dir/$output_name" \
    -D "part=\"$part_name\"" \
    "$source_file"
}

render_part front front_chassis_v3.stl
render_part display_retainer display_retainer_low_profile.stl
render_part carrier electronics_carrier_photo_corrected.stl
render_part rear rear_cap_compact.stl
render_part io_bezel io_bezel_hood.stl
render_part io_slider_open io_slider_universal.stl
render_part io_slider_fitted io_slider_fitted.stl
render_part io_slider_blank io_slider_blank.stl
render_part io_backer io_backer.stl
render_part io_wall_coupon io_wall_coupon.stl
render_part retainer_latch_wall retainer_latch_coupon_wall.stl
render_part retainer_latch_tab retainer_latch_coupon_tab.stl
render_part stack_spacers stack_spacing_blocks_13p1mm.stl
render_part rear_door rear_service_door_cabled.stl
render_part rear_door_closed rear_service_door_closed.stl
render_part seam_draw_key seam_draw_key_nominal.stl
render_part seam_draw_key_loose seam_draw_key_loose.stl
render_part seam_draw_key_snug seam_draw_key_snug.stl
render_part closure_coupon_front closure_coupon_front.stl
render_part closure_coupon_rear closure_coupon_rear.stl
render_part display_pins display_locator_pins.stl
render_part nu08_pins nu08_snap_pins.stl
render_part cable_keeper display_cable_keeper.stl
render_part carrier_gauge carrier_alignment_gauge.stl
render_part pin_coupon pin_fit_coupon.stl

python3 "$script_dir/validate_stl.py" "$output_dir"/*.stl
echo "Exported and validated PiRouter SnapShell v3 parts in $output_dir"
