/*
 * PiRouter SnapShell v3
 *
 * Support-free, screw-less printed enclosure for:
 *   - Original official Raspberry Pi 7-inch Touch Display (800 x 480)
 *   - Raspberry Pi 5 + active cooler
 *   - 52Pi NU08 / EP-0213 (115 x 95 mm)
 *   - 2.5-inch SATA SSD
 *
 * The electronics remain assembled with their normal standoffs/screws. The
 * printed enclosure uses replaceable alignment pins in the display/NU08's
 * natural mounting holes, a carrier at the NU08 PCB plane, and removable
 * positive-lock seam draw-keys. All large port openings are U-notches open to
 * a mating edge.
 *
 * Units: millimetres.
 */

$fn = $preview ? 28 : 64;

// assembly, exploded, section, front, display_retainer, carrier, rear,
// io_bezel, io_slider_open, io_slider_fitted, io_slider_blank, io_backer,
// io_wall_coupon, retainer_latch_wall, retainer_latch_tab, rear_door,
// stack_spacers, rear_door_closed, seam_draw_key, seam_draw_keys,
// seam_draw_key_loose, seam_draw_key_snug, closure_coupon_front,
// closure_coupon_rear, closure_coupon_assembly, display_pins, nu08_pins,
// pin_coupon, carrier_gauge, cable_keeper, hardware
part = "assembly";
explode = 7;
show_hardware = true;
show_accessories = true;

// ---------- Printer/material tuning ----------
fit = 0.35;
wall = 2.40;
front_skin = 3.00;
back_skin = 2.80;
plate_t = 2.60;

// ---------- Overall architecture ----------
case_w = 204.0;
case_h = 128.0;
case_r = 7.0;
front_depth = 26.0; // glass/front to NU08 carrier seam
rear_depth = 34.0;  // NU08 carrier seam to rear exterior

// A small downward bias preserves the display-tail exit without a deep chin.
display_y = 2.0;

// ---------- Official display geometry ----------
display_w = 192.96;
display_h = 110.76;
display_total_t = 6.0;
display_clear = 0.45;
glass_overlap = 1.55;
display_pocket_z = 10.5;

// Official external M3 mounting pattern. Thread minor diameter is smaller than
// 3 mm, so locator noses default to 2.30 mm and intentionally do not thread.
display_m3_dx = 126.20;
display_m3_dy = 65.65;
// Official tail-down orientation: the M3 pattern centre is (-0.84,+0.975)
// from the glass centre. Keeping the tail at the lower I/O side avoids an
// undocumented 180-degree assumption.
display_hole_centre_x = -0.84;
display_hole_centre_y = display_y + 0.975;
display_locator_d = 2.30;

// ---------- NU08 / Pi / SSD geometry ----------
// Verified NU08 outline from the manufacturer's dimensioned product image.
nu08_w = 115.0; // horizontal after rotating the board 90 degrees
nu08_h = 95.0;
// Photo-verified landscape placement. The complete stack is shifted 17 mm
// left from v2 so the SSD clears the old right-side obstruction and the port
// bank lands well away from a rounded case corner.
nu08_cx = 21.0;
nu08_cy = -3.0;
nu08_pcb_t = 1.60;
nu08_board_gap = 0.30;

// Original NU08 coordinates are 95 (u) x 115 (v). Outer M2.5 holes are
// approximately 4 mm from each edge. Keep these parameters easy to calibrate.
nu08_hole_u = [4.0, 91.0];
nu08_hole_v = [4.0, 111.0];
nu08_hole_d = 2.70;

// A standard 2.5-inch drive is 100.35 x 69.85; use the thicker 9.5 mm case.
ssd_w = 100.35;
ssd_h = 69.85;
ssd_t = 9.50;
ssd_cx = 24.5;
// The SSD's lower edge aligns with the NU08/Pi lower edge in the assembled
// 52Pi bracket, as the fit photos show.
ssd_cy = -15.575;

// Photo-verified orientation: Pi occupies the NU08's negative-X (left) end.
// Ethernet/USB face the lower wall and USB-C/HDMI face negative X.
pi_w = 56.0;
pi_h = 85.0;
pi_cx = -8.5;
pi_cy = -8.0;
pi_pcb_t = 1.60;
pi_standoff = 11.0;
cooler_w = 58.0;
cooler_h = 45.0;
cooler_z = 19.0;

// The seam carrier supports the NU08 just behind its rear face; it does not
// intersect the PCB. Four printed collars define the 0.30 mm stand-off.
carrier_front_z = front_depth;
nu08_plane_z = carrier_front_z + plate_t + nu08_board_gap + nu08_pcb_t / 2;
pi_plane_z = nu08_plane_z + pi_standoff;
case_total_z = front_depth + plate_t + rear_depth;

// In rear-cap local print coordinates, z=0 is the rear exterior/bed and the
// open seam is z=rear_depth.
pi_plane_rear_z = case_total_z - pi_plane_z;
// Pi 5 USB stacks reach about 17.5 mm above the PCB datum. Leave 19 mm plus
// tolerance, while retaining a solid strip above the rear exterior skin.
port_notch_floor = max(back_skin + 0.8, pi_plane_rear_z - 19.0);

// ---------- Adjustable I/O cassette ----------
// The rear-cap U-notch is deliberately larger than the uncertain connector
// envelope. A broad mask floats behind the hooded bezel, giving real physical
// X/Z adjustment without reprinting either large shell.
io_cx = -8.0;
io_notch_w = 90.0;
io_notch_h = rear_depth - port_notch_floor;
io_spigot_w = io_notch_w - 2 * fit;
io_spigot_h = io_notch_h - 2 * fit;
io_field_w = 82.0;
io_field_h = io_notch_h - 3.4;
io_bezel_w = 100.0;
io_bezel_h = io_notch_h + 5.2;
io_hood_depth = 8.0;
io_spigot_depth = wall + 0.8;
io_slider_w = 110.0;
io_slider_h = 36.0;
io_open_w = 64.0;
io_open_h = 22.0;
io_pin_x = 42.5;
io_pin_y = 8.0;
io_slider_lift = 4.0;
io_open_y = -io_slider_lift - 2.0;

// ---------- Helpers ----------
module rounded_rect_2d(size = [10, 10], r = 2) {
    rr = min(r, min(size[0], size[1]) / 2);
    hull()
        for (x = [-size[0] / 2 + rr, size[0] / 2 - rr])
            for (y = [-size[1] / 2 + rr, size[1] / 2 - rr])
                translate([x, y]) circle(r = rr);
}

module rounded_prism(size = [10, 10, 2], r = 2) {
    linear_extrude(height = size[2]) rounded_rect_2d([size[0], size[1]], r);
}

module box_at(c = [0, 0, 0], s = [1, 1, 1]) {
    translate(c) cube(s, center = true);
}

module ring_2d(outer = [20, 20], inner = [16, 16], r = 3) {
    difference() {
        rounded_rect_2d(outer, r);
        rounded_rect_2d(inner, max(0.8, r - (outer[0] - inner[0]) / 2));
    }
}

module capsule_2d(length = 12, diameter = 3, facets = 24) {
    hull() {
        translate([-(length - diameter) / 2, 0])
            circle(d = diameter, $fn = facets);
        translate([(length - diameter) / 2, 0])
            circle(d = diameter, $fn = facets);
    }
}

module tapered_pin(d1 = 3.0, d2 = 2.6, h = 2.0) {
    cylinder(d1 = d1, d2 = d2, h = h);
}

function nu08_world(u, v) = [
    nu08_cx + nu08_w / 2 - v,
    nu08_cy + u - nu08_h / 2
];

display_holes = [
    [display_hole_centre_x - display_m3_dx / 2,
     display_hole_centre_y - display_m3_dy / 2],
    [display_hole_centre_x + display_m3_dx / 2,
     display_hole_centre_y - display_m3_dy / 2],
    [display_hole_centre_x - display_m3_dx / 2,
     display_hole_centre_y + display_m3_dy / 2],
    [display_hole_centre_x + display_m3_dx / 2,
     display_hole_centre_y + display_m3_dy / 2]
];

shell_dowels = [
    [-72, case_h / 2 - wall / 2],
    [ 28, case_h / 2 - wall / 2],
    [-case_w / 2 + wall / 2, -38],
    [-case_w / 2 + wall / 2,  34],
    [ case_w / 2 - wall / 2, -38],
    [ case_w / 2 - wall / 2,  34]
];

// ---------- Positive-lock seam draw-key sockets ----------
// The old bead/C-clips depended only on friction and could slide straight off.
// Each new socket is a blind sliding dovetail. A flat dumbbell-shaped key has
// thick heads buried in both shells and a narrow waist across the carrier, so
// it cannot leave in Z or peel outward; it can only slide back to the entry.
closure_track_l = 24.0;
closure_track_stop = 2.0;
closure_socket_z = 5.80;
closure_groove_z = 4.80;
closure_mouth_z = 0.80;
closure_ramp_z = 0.80;
closure_groove_mouth = 1.55;
closure_groove_deep = 2.35;
closure_flange_t = 1.80;
closure_boss_out = closure_groove_deep + closure_flange_t;
closure_key_l = 19.50;
closure_key_engage = 4.50;
closure_key_clear = 0.40;
closure_key_draw = 0.10;
closure_latch_extra = 0.25;

module closure_boss_profile_2d() {
    polygon(points = [
        [-0.30, -0.05],
        [closure_boss_out, -0.05],
        [closure_boss_out, closure_socket_z],
        [-0.30, closure_socket_z]
    ]);
}

module closure_groove_profile_2d() {
    // Coordinates are [outward distance, distance down from the seam]. The
    // groove narrows toward the seam; the 45-degree change prints without
    // support on either large shell.
    polygon(points = [
        [-0.02, -0.10],
        [closure_groove_mouth, -0.10],
        [closure_groove_mouth, closure_mouth_z],
        [closure_groove_deep, closure_mouth_z + closure_ramp_z],
        [closure_groove_deep, closure_groove_z],
        [-0.02, closure_groove_z]
    ]);
}

module closure_window_profile_2d() {
    // A short release window through the outer flange. The 4.3 mm roof is a
    // routine bridge for a 0.4 mm nozzle and needs no slicer support.
    polygon(points = [
        [closure_groove_deep - 0.10, 1.85],
        [closure_boss_out + 0.10, 1.85],
        [closure_boss_out + 0.10, 4.15],
        [closure_groove_deep - 0.10, 4.15]
    ]);
}

// Extrude a profile whose 2D axes are outward distance and distance down from
// the open seam. The third axis follows an enclosure Y edge.
module closure_edge_y(x_open, y_sign, z_top, u_sign, length) {
    multmatrix([
        [0,       0, u_sign, x_open],
        [y_sign,  0, 0,      y_sign * case_h / 2],
        [0,      -1, 0,      z_top],
        [0,       0, 0,      1]
    ]) linear_extrude(height = length) children();
}

// Same profile mapping for an enclosure X edge.
module closure_edge_x(x_sign, y_open, z_top, u_sign, length) {
    multmatrix([
        [x_sign, 0, 0,      x_sign * case_w / 2],
        [0,      0, u_sign, y_open],
        [0,     -1, 0,      z_top],
        [0,      0, 0,      1]
    ]) linear_extrude(height = length) children();
}

module closure_socket_y(x_open, y_sign, z_top, u_sign = 1) {
    difference() {
        closure_edge_y(x_open, y_sign, z_top, u_sign, closure_track_l)
            closure_boss_profile_2d();
        closure_edge_y(
            x_open - u_sign * 0.10, y_sign, z_top, u_sign,
            closure_track_l - closure_track_stop + 0.20
        ) closure_groove_profile_2d();
        closure_edge_y(
            x_open + u_sign * 2.20, y_sign, z_top, u_sign, 4.30
        ) closure_window_profile_2d();
    }
}

module closure_socket_x(x_sign, y_open, z_top, u_sign = 1) {
    difference() {
        closure_edge_x(x_sign, y_open, z_top, u_sign, closure_track_l)
            closure_boss_profile_2d();
        closure_edge_x(
            x_sign, y_open - u_sign * 0.10, z_top, u_sign,
            closure_track_l - closure_track_stop + 0.20
        ) closure_groove_profile_2d();
        closure_edge_x(
            x_sign, y_open + u_sign * 2.20, z_top, u_sign, 4.30
        ) closure_window_profile_2d();
    }
}

module front_closure_sockets(z_top) {
    // Two upper locks, two short lower locks outside the adjustable I/O
    // cassette/backer, and one lock on each short side.
    closure_socket_y(-48 - closure_track_l / 2,  1, z_top,  1);
    closure_socket_y( 48 + closure_track_l / 2,  1, z_top, -1);
    closure_socket_y(-79 - closure_track_l / 2, -1, z_top,  1);
    closure_socket_y( 64 + closure_track_l / 2, -1, z_top, -1);
    closure_socket_x(-1, -closure_track_l / 2, z_top, 1);
    closure_socket_x( 1, -closure_track_l / 2, z_top, 1);
}

module rear_closure_sockets(z_top) {
    // rear_cap() is printed back-down and rotated 180 degrees about X during
    // installation. Mirror Y and the side-track slide direction here.
    closure_socket_y(-48 - closure_track_l / 2, -1, z_top,  1);
    closure_socket_y( 48 + closure_track_l / 2, -1, z_top, -1);
    closure_socket_y(-79 - closure_track_l / 2,  1, z_top,  1);
    closure_socket_y( 64 + closure_track_l / 2,  1, z_top, -1);
    closure_socket_x(-1,  closure_track_l / 2, z_top, -1);
    closure_socket_x( 1,  closure_track_l / 2, z_top, -1);
}

// ---------- Main front chassis ----------
module front_chassis() {
    difference() {
        union() {
            rounded_prism([case_w, case_h, front_depth], case_r);
            front_closure_sockets(front_depth);
            // Tapered seam dowels grow upward in the print direction.
            for (p = shell_dowels)
                translate([p[0], p[1], front_depth - 0.05])
                    tapered_pin(3.2, 2.75, plate_t + 2.0);
        }

        // Touch opening through the front skin.
        translate([0, display_y, -0.1])
            linear_extrude(height = front_skin + 0.2)
                rounded_rect_2d([
                    display_w - 2 * glass_overlap,
                    display_h - 2 * glass_overlap
                ], 2.0);

        // Glass/display pocket.
        translate([0, display_y, front_skin])
            linear_extrude(height = display_pocket_z - front_skin + 0.1)
                rounded_rect_2d([
                    display_w + 2 * display_clear,
                    display_h + 2 * display_clear
                ], 6.5);

        // Behind the display, the cavity expands to the full chassis interior.
        translate([0, 0, display_pocket_z])
            linear_extrude(height = front_depth - display_pocket_z + 0.2)
                rounded_rect_2d([
                    case_w - 2 * wall,
                    case_h - 2 * wall
                ], case_r - wall);

        // Four shallow side-wall pockets capture the retainer's long,
        // in-plane flexures. Each pocket leaves 1 mm of exterior wall and is
        // only 1.5 mm deep, so its tiny bridge is shorter than one wall.
        for (sx = [-1, 1])
            for (cy = [-38, 38]) {
                py = cy + (cy < 0 ? -7 : 7);
                box_at([
                    sx * (case_w / 2 - wall + 0.65), py,
                    display_pocket_z + retainer_t / 2
                ], [1.50, 6.8, retainer_t + 0.4]);
            }

        // v2 cut a second, empty bridge opening through the display half of
        // the enclosure. The real U-bridge lives entirely rearward of the
        // NU08 carrier, so v3 keeps this lower front wall continuous.
    }
}

// ---------- Display retainer ----------
retainer_w = 184.0;
retainer_h = 114.0;
retainer_t = 2.40;
ribbon_gate_x = 15.0;
ribbon_gate_y = display_y + 48.0;
ribbon_post_dx = 28.0;

module ribbon_keeper_post() {
    // A bed-supported post with a tapered head. The separate keeper rests on
    // the 1.15 mm-high shoulder, leaving a generous unpinched FFC gap.
    cylinder(d = 8.0, h = 1.15);
    translate([0, 0, 1.1]) cylinder(d = 6.0, h = 1.85);
    translate([0, 0, 2.95]) cylinder(d1 = 6.0, d2 = 7.1, h = 0.60);
    translate([0, 0, 3.55]) cylinder(d1 = 7.1, d2 = 6.0, h = 0.60);
}

module display_retainer_plate() {
    difference() {
      union() {
        // Perimeter skeleton stays outside the display controller board.
        translate([0, display_y, 0])
            linear_extrude(height = retainer_t)
                ring_2d([retainer_w, retainer_h], [164, 92], 5.0);

        // Four pin pads and proven vertical arms stay outside the controller
        // board. Their low 2.4 mm profile leaves over 5 mm to the SSD face.
        for (p = display_holes) {
            translate([p[0], p[1], 0]) cylinder(d = 11, h = retainer_t);
            y_edge = p[1] < display_hole_centre_y
                ? display_y - retainer_h / 2 + 5
                : display_y + retainer_h / 2 - 5;
            box_at([p[0], (p[1] + y_edge) / 2, retainer_t / 2],
                   [8, abs(p[1] - y_edge) + 2, retainer_t]);
        }
      }

      // The panel's built-in flex exits at lower centre. This rim-open channel
      // keeps the full tail/fold region free of hard plastic or print bridges.
      box_at([0, display_y - retainer_h / 2 + 3, retainer_t / 2],
             [64, 22, retainer_t + 0.2]);
    }
}

module retainer_side_latch(sx = 1, cy = 38) {
    free_y = cy + (cy < 0 ? -7 : 7);
    anchor_y = cy + (cy < 0 ? 9 : -9);

    // The 20 mm beam flexes in the XY layer plane, not between Z layers.
    box_at([sx * 96.4, cy, retainer_t / 2],
           [1.65, 20.0, retainer_t]);
    // Short root ties the beam into the perimeter rail.
    box_at([sx * 94.2, anchor_y, retainer_t / 2],
           [5.8, 3.2, retainer_t]);

    // The leading face grows outward by less than 45 degrees as it prints.
    // During insertion that ramp cams the long beam inward; it springs into
    // the shallow front-shell pocket at the final display datum.
    hull() {
        box_at([sx * 97.25, free_y, 0.25], [1.40, 5.6, 0.50]);
        box_at([sx * 99.60, free_y, retainer_t - 0.25],
               [0.50, 5.6, 0.50]);
    }
}

module display_retainer() {
    difference() {
        union() {
            display_retainer_plate();

            // Four low-profile wall latches replace v2's 18 mm towers. The
            // lower-right tower was directly inside the SSD/NU08 envelope and
            // is the obstruction visible in the fit photos.
            for (sx = [-1, 1])
                for (cy = [-38, 38]) retainer_side_latch(sx, cy);

            // The DSI cable leaves the crowded display/SSD volume over the
            // SSD's upper edge, crosses the open carrier centre, then turns
            // toward the Pi. These two posts form a removable 18 mm-wide gate
            // with a 1.15 mm unpinched gap under its keeper.
            for (x = [-ribbon_post_dx / 2, ribbon_post_dx / 2])
                translate([ribbon_gate_x + x, ribbon_gate_y,
                           retainer_t - 0.05])
                    ribbon_keeper_post();

        }

        // Through-holes accept separate replaceable display locator pins.
        for (p = display_holes)
            translate([p[0], p[1], -0.1])
                cylinder(d = 3.05 + fit, h = retainer_t + 0.2);
    }
}

// ---------- Electronics carrier at the NU08 plane ----------
module carrier_outer_ring() {
    linear_extrude(height = plate_t)
        difference() {
            // This is a true mid-frame, not a loose plate hidden inside the
            // shells. It fills the entire 2.6 mm seam, seals the perimeter,
            // and is positively captured between front and rear caps.
            ring_2d([
                case_w - 0.40,
                case_h - 0.40
            ], [case_w - 22, case_h - 22], case_r - wall);

            // Clear the exact board outline; only the four intentional
            // natural-hole pads/collars are allowed to touch the NU08.
            translate([nu08_cx, nu08_cy])
                square([nu08_w + 2 * fit, nu08_h + 2 * fit], center = true);

            // Keep the photo-verified lower port bank and U-bridge completely
            // open through the seam ring. The extra width also clears the
            // physically floating mask/backer where they cross the seam on
            // the wall's interior side.
            translate([io_cx, -case_h / 2 + 5])
                square([io_slider_w + 6, 18], center = true);
        }
}

module port_orientation_arrow() {
    // A hardware-safe mechanical/visual key on the upper perimeter points to
    // the only supported port-facing edge. It cannot hide beneath the board.
    linear_extrude(height = 0.55)
        polygon(points = [
            [-3.0, 5.0], [3.0, 5.0], [3.0, 0.0],
            [7.0, 0.0], [0.0, -8.0], [-7.0, 0.0], [-3.0, 0.0]
        ]);
}

module electronics_carrier() {
    difference() {
        union() {
            carrier_outer_ring();

            // Natural-hole pads and spokes. Bottom-row pads connect to the
            // lower rim; upper-row pads connect to the upper rim, leaving the
            // SSD, PCIe FFC, DSI, and GPIO cable regions open.
            for (u = nu08_hole_u)
                for (v = nu08_hole_v) {
                    p = nu08_world(u, v);
                    translate([p[0], p[1], 0]) cylinder(d = 10, h = plate_t);
                    translate([p[0], p[1], plate_t - 0.05])
                        cylinder(d = 7.0, h = nu08_board_gap + 0.05);
                    // The lower-left pad sits inside the enlarged I/O cut, so
                    // route that one sideways to the left rim. Every other
                    // pad uses the shortest clear vertical spoke.
                    if (p[1] < nu08_cy &&
                        p[0] < io_cx + io_notch_w / 2) {
                        x_edge = -case_w / 2 + 6;
                        box_at([(p[0] + x_edge) / 2, p[1], plate_t / 2],
                               [abs(p[0] - x_edge) + 2, 8, plate_t]);
                    } else {
                        y_edge = p[1] < nu08_cy
                            ? -case_h / 2 + 6 : case_h / 2 - 6;
                        box_at([p[0], (p[1] + y_edge) / 2, plate_t / 2],
                               [8, abs(p[1] - y_edge) + 2, plate_t]);
                    }
                }

            translate([0, case_h / 2 - 5.5, plate_t - 0.05])
                port_orientation_arrow();
        }

        // Replaceable NU08 pin sockets.
        for (u = nu08_hole_u)
            for (v = nu08_hole_v) {
                p = nu08_world(u, v);
                translate([p[0], p[1], -0.1])
                    cylinder(d = 3.00 + fit,
                             h = plate_t + nu08_board_gap + 0.2);
            }

        // Shell-alignment dowels pass through the carrier.
        for (p = shell_dowels)
            translate([p[0], p[1], -0.1]) cylinder(d = 3.45, h = plate_t + 0.2);
    }
}

// ---------- Rear cap ----------
module rear_vent_slots() {
    // Photo-corrected cooler exhaust over the Pi on the NU08's left end.
    for (x = [pi_cx - 24 : 8 : pi_cx + 16])
        for (y = [pi_cy - 18 : 9 : pi_cy + 18])
            translate([x, -y, -0.1])
                linear_extrude(height = back_skin + 0.2)
                    capsule_2d(7.2, 3.2);

    // A second field ventilates the exposed NU08/SSD half without weakening
    // the service-door or I/O-cassette edges.
    for (x = [40 : 12 : 76])
        for (y = [-38 : 12 : 34])
            translate([x, -y, -0.1])
                linear_extrude(height = back_skin + 0.2)
                    capsule_2d(8.0, 3.0);
}

module rear_cap() {
    difference() {
        union() {
            difference() {
                rounded_prism([case_w, case_h, rear_depth], case_r);
                translate([0, 0, back_skin])
                    linear_extrude(height = rear_depth - back_skin + 0.2)
                        rounded_rect_2d([
                            case_w - 2 * wall,
                            case_h - 2 * wall
                        ], case_r - wall);
            }
            rear_closure_sockets(rear_depth);

        }

        rear_vent_slots();

        // Bottom Ethernet/USB/rigid-bridge bay. It is open to the front seam.
        box_at([io_cx, case_h / 2,
                (port_notch_floor + rear_depth + 0.2) / 2],
               [io_notch_w, wall + 4,
                rear_depth - port_notch_floor + 0.4]);

        // Rear service opening: the Pi's USB-C and HDMI edge faces inward over
        // the NU08, so a side-wall cutout cannot line up. This opening provides
        // straight finger/plug access and room for a 90-degree cable turn.
        translate([-57, -7, -0.1])
            linear_extrude(height = back_skin + 0.2)
                rounded_rect_2d([62, 56], 5.0);

        // Open-ended sockets receive front-chassis seam dowels.
        for (p = shell_dowels)
            translate([p[0], -p[1], rear_depth - 2.25])
                cylinder(d1 = 3.55, d2 = 3.30, h = 2.4);

    }
}

// ---------- Screwless, physically adjustable I/O cassette ----------
io_slider_t = 0.80;
io_backer_t = 1.20;
io_pin_d = 1.90;
service_door_w = 66.0;
service_door_h = 60.0;
door_side_beam_x = 30.2;
door_end_beam_y = 27.2;

module io_bezel() {
    difference() {
        union() {
            // The complete exterior hood grows vertically from its outward
            // face. It protects the uncertain U-bridge protrusion while every
            // layer remains supported by the layer below.
            linear_extrude(height = io_hood_depth)
                ring_2d([io_bezel_w, io_bezel_h],
                        [io_field_w, io_field_h], 2.4);

            // A 0.35 mm-per-side spigot locates in the rear-cap U-notch and
            // projects 0.8 mm past the wall to locate the floating mask.
            translate([0, 0, io_hood_depth - 0.05])
                linear_extrude(height = io_spigot_depth + 0.05)
                    difference() {
                        rounded_rect_2d([io_spigot_w, io_spigot_h], 1.8);
                        rounded_rect_2d([io_field_w, io_field_h], 1.3);
                    }

            // Four small tapered pins pass through generous slider slots and
            // friction-locate the inner backer. The closed shell traps them.
            for (sx = [-1, 1])
                for (sy = [-1, 1])
                    translate([
                        sx * io_pin_x, sy * io_pin_y,
                        io_hood_depth + io_spigot_depth - 0.05
                    ]) cylinder(d1 = io_pin_d,
                                d2 = io_pin_d - 0.20,
                                h = io_slider_t + io_backer_t + 0.35);
        }

        // One uninterrupted field cannot foul the RJ45 latch, LEDs, stacked
        // USB shells, or the rigid bridge.
        translate([0, 0, -0.1])
            linear_extrude(height = io_hood_depth + io_spigot_depth + 0.2)
                rounded_rect_2d([io_field_w, io_field_h], 1.3);

        // Continue the bridge lane through the bezel itself and out to the
        // carrier seam. Without this U-leg, a nominally open slider would
        // still leave 1.7 mm of hood frame in the bridge's path.
        box_at([2.0, io_bezel_h / 2,
                (io_hood_depth + io_spigot_depth) / 2],
               [30.0, io_bezel_h - io_field_h + 2.0,
                io_hood_depth + io_spigot_depth + 0.2]);
    }
}

module io_slider(style = "open") {
    difference() {
        rounded_prism([io_slider_w, io_slider_h, io_slider_t], 1.6);

        // Slots permit about +/-8 mm X and +/-2 mm Z travel around the bezel
        // pins. Vertical travel is biased away from the rear skin, then the
        // aperture is offset back by 2 mm so its nominal centre is unchanged.
        for (sx = [-1, 1])
            for (sy = [-1, 1])
                box_at([sx * io_pin_x,
                        sy * io_pin_y - io_slider_lift,
                        io_slider_t / 2],
                       [18.0, 8.0, io_slider_t + 0.2]);

        if (style == "open") {
            translate([0, io_open_y, -0.1])
                linear_extrude(height = io_slider_t + 0.2)
                    rounded_rect_2d([io_open_w, io_open_h], 1.5);

            // The rigid bridge begins at the carrier seam below the connector
            // faces. This 30 mm rim-open leg gives it a full-height escape
            // without enlarging the complete port window.
            box_at([2.0, io_slider_h / 2 - 7.0, io_slider_t / 2],
                   [30.0, 14.2, io_slider_t + 0.2]);
        } else if (style == "fitted") {
            // Corrected photo map; local centres remain -15.8, +3, +21 mm
            // after moving both the stack and cassette to their v3 datums.
            for (x = [-15.8, 3.0, 21.0])
                translate([x, io_open_y, -0.1])
                    linear_extrude(height = io_slider_t + 0.2)
                        rounded_rect_2d([
                            x == -15.8 ? 21.0 : 14.5,
                            io_open_h
                        ], 1.4);

            box_at([2.0, io_slider_h / 2 - 7.0, io_slider_t / 2],
                   [30.0, 14.2, io_slider_t + 0.2]);
        }

        // This rim-open notch always faces the carrier seam and identifies
        // the slider's intentionally biased vertical orientation.
        box_at([0, io_slider_h / 2, io_slider_t / 2],
               [5.0, 2.4, io_slider_t + 0.2]);
    }
}

module io_backer() {
    difference() {
        linear_extrude(height = io_backer_t)
            ring_2d([io_slider_w + 4.0, io_slider_h],
                    [io_field_w - 0.4, io_field_h - 0.4], 2.0);
        for (sx = [-1, 1])
            for (sy = [-1, 1])
                translate([sx * io_pin_x, sy * io_pin_y, -0.1])
                    cylinder(d = io_pin_d - 0.10,
                             h = io_backer_t + 0.2);

        // Match the bridge U-leg in both mask styles and the outer bezel.
        box_at([2.0, io_slider_h / 2, io_backer_t / 2],
               [30.0, io_slider_h - (io_field_h - 0.4) + 2.0,
                io_backer_t + 0.2]);
    }
}

module io_wall_coupon() {
    coupon_w = io_notch_w + 14.0;
    coupon_h = io_notch_h + 4.0;
    difference() {
        rounded_prism([coupon_w, coupon_h, wall], 2.0);
        // Same rim-open U-notch as the rear cap, with a 4 mm bottom rail.
        box_at([0, 2.0, wall / 2],
               [io_notch_w, io_notch_h + 0.2, wall + 0.2]);
    }
}

module retainer_latch_coupon_wall() {
    difference() {
        union() {
            // Inner surface is x=0; +X is the exterior side of the wall.
            box_at([wall / 2, 0, 5.0], [wall, 24.0, 10.0]);
            // Small foot represents the display-pocket landing and makes the
            // tall coupon stable on the bed.
            box_at([-3.0, 0, 0.5], [8.4, 24.0, 1.0]);
        }
        box_at([0.75, 7.0, 2.40], [1.50, 6.8, retainer_t + 0.4]);
    }
}

module retainer_latch_coupon_tab() {
    // Same 20 mm beam, root, and insertion ramp as the production retainer,
    // with only a small thumb pad added in place of the full perimeter rail.
    translate([-99.6, 0, 0]) {
        retainer_side_latch(1, 0);
        box_at([90.0, -9.0, retainer_t / 2],
               [10.0, 8.0, retainer_t]);
    }
}

module stack_spacing_blocks() {
    stack_test_gap = front_depth - display_pocket_z - retainer_t;
    for (x = [0 : 10 : 30])
        translate([x, 0, 0]) rounded_prism([6.0, 6.0, stack_test_gap], 1.0);
    // A bed-level breakaway web keeps the four identical gauges together.
    box_at([15, 0, 0.20], [36.0, 1.2, 0.40]);
}

module door_side_flexure_slots(x_sign = 1) {
    x0 = x_sign * door_side_beam_x;
    for (dx = [-1.4, 1.4])
        box_at([x0 + dx, 0, 1.2], [0.8, 17.0, 2.6]);
    box_at([x0, 8.9, 1.2], [3.6, 0.8, 2.6]);
}

module door_end_flexure_slots(y_sign = 1) {
    y0 = y_sign * door_end_beam_y;
    for (dy = [-1.4, 1.4])
        box_at([0, y0 + dy, 1.2], [17.0, 0.8, 2.6]);
    box_at([8.9, y0, 1.2], [0.8, 3.6, 2.6]);
}

module door_side_hook(x_sign = 1) {
    translate([x_sign * door_side_beam_x, 5.5, 2.25]) {
        box_at([0, 0, (back_skin + 1.0) / 2],
               [1.2, 4.0, back_skin + 1.0]);
        hull() {
            box_at([0, 0, back_skin + 0.10], [1.2, 4.0, 0.35]);
            box_at([x_sign * 0.40, 0, back_skin + 0.70],
                   [1.3, 4.0, 0.35]);
        }
    }
}

module door_end_hook(y_sign = 1) {
    translate([5.5, y_sign * door_end_beam_y, 2.25]) {
        box_at([0, 0, (back_skin + 1.0) / 2],
               [4.0, 1.2, back_skin + 1.0]);
        hull() {
            box_at([0, 0, back_skin + 0.10], [4.0, 1.2, 0.35]);
            box_at([0, y_sign * 0.40, back_skin + 0.70],
                   [4.0, 1.3, 0.35]);
        }
    }
}

module rear_service_door(cabled = true) {
    difference() {
        rounded_prism([service_door_w, service_door_h, 2.4], 6.0);

        // Three rim-open exits let USB-C and both micro-HDMI leads remain
        // installed. Use part="rear_door_closed" when no permanent leads need
        // to cross the door. Rim-open slots never bridge during printing.
        if (cabled)
            for (y = [-18, 0, 18])
                box_at([-service_door_w / 2, y, 1.2], [18, 8, 2.6]);

        // U-slots make the catches flex in the XY layer plane over a 17 mm
        // beam. The old 3.4 mm-tall Z-layer cantilevers were too brittle.
        door_side_flexure_slots(1);
        if (!cabled) door_side_flexure_slots(-1);
        for (y_sign = [-1, 1]) door_end_flexure_slots(y_sign);

        // Vent slots keep the door from becoming a hot spot beside the cooler.
        for (x = [-12, 0, 12])
            for (y = [-18, 0, 18])
                translate([x, y, -0.1])
                    linear_extrude(height = 2.6) capsule_2d(8, 3);
    }

    // A 0.30 mm nominal barb interference moves with each long in-plane beam.
    // The cabled version omits the left catch; right/top/bottom still constrain
    // every axis. The closed version uses all four.
    for (x_sign = cabled ? [1] : [-1, 1])
        door_side_hook(x_sign);
    for (y_sign = [-1, 1])
        door_end_hook(y_sign);
}

// One-layer-ish board gauge: verify the image-derived NU08 hole positions
// before printing the structural carrier or any snap pins.
module carrier_alignment_gauge() {
    gauge_t = 1.0;
    difference() {
        union() {
            translate([nu08_cx, nu08_cy, 0])
                linear_extrude(height = gauge_t)
                    // The frame stays outside the PCB outline, avoiding edge
                    // components on a fully assembled NU08. Only the four
                    // natural-hole pads overlap the board surface.
                    ring_2d([nu08_w + 7.0, nu08_h + 7.0],
                            [nu08_w + 0.8, nu08_h + 0.8], 3.0);
            for (u = nu08_hole_u)
                for (v = nu08_hole_v) {
                    p = nu08_world(u, v);
                    translate([p[0], p[1], 0]) cylinder(d = 9.2, h = gauge_t);
                }
        }
        for (u = nu08_hole_u)
            for (v = nu08_hole_v) {
                p = nu08_world(u, v);
                translate([p[0], p[1], -0.1])
                    cylinder(d = nu08_hole_d, h = gauge_t + 0.2);
            }
    }
}

// ---------- Replaceable pins ----------
module display_locator_pin() {
    union() {
        cylinder(d = 6.4, h = 1.0);
        translate([0, 0, 0.95]) cylinder(d = 3.05, h = retainer_t + 0.25);
        translate([0, 0, retainer_t + 1.15])
            cylinder(d1 = display_locator_d, d2 = display_locator_d - 0.35, h = 2.3);
    }
}

module nu08_snap_pin(stem_d = 2.40) {
    difference() {
        union() {
            cylinder(d = 6.2, h = 0.9);
            // When installed, the base top is flush with the carrier's front
            // face. The 3 mm shank ends at the stand-off collar/PCB surface.
            translate([0, 0, 0.85])
                cylinder(d = 3.00, h = plate_t + nu08_board_gap + 0.05);
            // One continuous revolved profile avoids coplanar surfaces at the
            // stem/barb union. Its maximum flare is 0.05 mm behind the PCB.
            rotate_extrude(convexity = 4)
                polygon(points = [
                    [0,      3.65],
                    [stem_d / 2, 3.65],
                    [stem_d / 2, 5.10],
                    [(stem_d + 0.55) / 2, 5.45],
                    [(stem_d - 0.15) / 2, 6.25],
                    [0,      6.25]
                ]);
        }
        // Split makes the board-end barb compliant.
        box_at([0, 0,
                0.9 + plate_t + nu08_board_gap + nu08_pcb_t / 2],
               [0.65, stem_d + 1.0, 3.6]);
    }
}

module pin_sprue(pin_type = "display") {
    for (x = [0 : 11 : 33])
        translate([x, 0, 0])
            if (pin_type == "display") display_locator_pin();
            else nu08_snap_pin();
    // 0.45 mm breakaway web keeps small pins together on the bed.
    translate([16.5, 0, 0.225]) cube([38, 1.2, 0.45], center = true);
}

module pin_fit_coupon() {
    difference() {
        rounded_prism([58, 38, 3.0], 3);
        // Top row calibrates natural-hole peg diameters.
        for (i = [0 : 3])
            translate([-21 + i * 14, 7, -0.1])
                cylinder(d = 2.40 + i * 0.15, h = 3.2);
        // Bottom row calibrates the 3.0/3.05 mm pin shanks in printed sockets.
        for (i = [0 : 2])
            translate([-14 + i * 14, -7, -0.1])
                cylinder(d = 3.20 + i * 0.15, h = 3.2);
    }
    // One to four top ticks identify 2.40, 2.55, 2.70, and 2.85 mm.
    for (i = [0 : 3])
        for (tick = [0 : i])
            box_at([-21 + i * 14 + (tick - i / 2) * 1.6, 15, 3.3],
                   [0.8, 3.0, 0.6]);
    // One to three bottom ticks identify 3.20, 3.35, and 3.50 mm.
    for (i = [0 : 2])
        for (tick = [0 : i])
            box_at([-14 + i * 14 + (tick - i / 2) * 1.6, -15, 3.3],
                   [0.8, 3.0, 0.6]);
}

// ---------- Removable positive-lock seam draw-key ----------
module seam_draw_key_profile_2d(
    clearance = closure_key_clear,
    normal_relief = 0,
    axial_relief = 0,
    draw = closure_key_draw
) {
    head_t = closure_groove_deep - clearance - normal_relief;
    waist_t = closure_groove_mouth - clearance - normal_relief;
    half_h = plate_t / 2 + closure_key_engage - axial_relief;
    waist_half = plate_t / 2 + closure_mouth_z - draw;
    head_half = waist_half + closure_ramp_z;

    // Flat underside is the print bed. The two heads are mechanically trapped
    // behind the socket mouths while the waist crosses the carrier band.
    polygon(points = [
        [-half_h, 0],
        [ half_h, 0],
        [ half_h, head_t],
        [ head_half, head_t],
        [ waist_half, waist_t],
        [-waist_half, waist_t],
        [-head_half, head_t],
        [-half_h, head_t]
    ]);
}

module seam_draw_key_extrude(
    length,
    x0 = 0,
    clearance = closure_key_clear,
    normal_relief = 0,
    axial_relief = 0,
    draw = closure_key_draw
) {
    // Map profile [axial, outward] and extrusion [slide] to a printable key
    // whose wall-facing side is flat on Z=0.
    multmatrix([
        [0, 0, 1, x0],
        [1, 0, 0, 0],
        [0, 1, 0, 0],
        [0, 0, 0, 1]
    ]) linear_extrude(height = length)
        seam_draw_key_profile_2d(
            clearance, normal_relief, axial_relief, draw
        );
}

module seam_draw_key_slice(
    x,
    clearance = closure_key_clear,
    normal_relief = 0,
    axial_relief = 0,
    draw = closure_key_draw
) {
    seam_draw_key_extrude(
        0.12, x, clearance, normal_relief, axial_relief, draw
    );
}

module seam_draw_key_body(clearance = closure_key_clear) {
    nose_l = 2.50;
    union() {
        seam_draw_key_extrude(
            closure_key_l - nose_l + 0.05, 0, clearance
        );
        // Reduced leading section starts easily, then cams both shell sockets
        // onto the key's 0.10 mm draw surfaces over a gentle 2.5 mm ramp.
        hull() {
            seam_draw_key_slice(
                closure_key_l - nose_l, clearance
            );
            seam_draw_key_slice(
                closure_key_l - 0.12, clearance, 0.30, 0.25, 0
            );
        }
    }
}

module seam_draw_key_latch_tooth(clearance = closure_key_clear) {
    head_t = closure_groove_deep - clearance;
    tooth_h = clearance + closure_latch_extra;
    tongue_y = -4.15;
    tooth_w = 1.35;

    // Shallow insertion ramp and steeper withdrawal face. Press the visible
    // tongue inward through the socket window before pulling the key out.
    multmatrix([
        [1, 0, 0, 0],
        [0, 0, 1, tongue_y - tooth_w / 2],
        [0, 1, 0, 0],
        [0, 0, 0, 1]
    ]) linear_extrude(height = tooth_w)
        polygon(points = [
            [0.90, head_t - 0.08],
            [1.42, head_t + tooth_h],
            [1.58, head_t + tooth_h],
            [3.55, head_t - 0.08]
        ]);
}

module seam_draw_key(clearance = closure_key_clear) {
    head_t = closure_groove_deep - clearance;
    waist_t = closure_groove_mouth - clearance;
    tongue_y = -4.15;
    tongue_w = 1.65;
    tongue_l = 11.50;
    latch_slot = 0.60;

    union() {
        difference() {
            union() {
                seam_draw_key_body(clearance);

                // A low central thumb tab remains about 2.7 mm outside the
                // track mouth at full insertion and cannot snag the flange.
                box_at([-2.0, 0, waist_t / 2],
                       [6.0, 5.4, waist_t]);
            }

            // Two open-ended slots isolate a long in-plane tongue. It bends
            // only 0.25 mm to pass the flange and is replaceable with the key.
            for (sy = [-1, 1])
                box_at([
                    tongue_l / 2 - 0.10,
                    tongue_y + sy * (tongue_w + latch_slot) / 2,
                    (head_t + clearance + closure_latch_extra) / 2
                ], [
                    tongue_l,
                    latch_slot,
                    head_t + clearance + closure_latch_extra + 0.30
                ]);
        }
        seam_draw_key_latch_tooth(clearance);
    }
}

// Full-depth two-piece coupon. The front half carries the exact 2.6 mm seam
// spacer and two locator pins; the rear half is printed back-down exactly like
// the production rear cap, then flipped onto those pins for a real pull test.
closure_coupon_w = 30.0;
closure_coupon_wall_d = 7.0;
closure_coupon_shell_z = 8.0;

module closure_coupon_front() {
    union() {
        translate([
            -closure_coupon_w / 2,
            -closure_coupon_wall_d,
            0
        ]) cube([
            closure_coupon_w,
            closure_coupon_wall_d,
            closure_coupon_shell_z
        ]);
        translate([0, -case_h / 2, 0])
            closure_socket_y(
                -closure_track_l / 2, 1, closure_coupon_shell_z, 1
            );
        box_at([
            0, -closure_coupon_wall_d / 2,
            closure_coupon_shell_z + plate_t / 2
        ], [24, 4.0, plate_t]);
        for (x = [-9, 9])
            translate([
                x, -closure_coupon_wall_d / 2,
                closure_coupon_shell_z + plate_t - 0.05
            ]) tapered_pin(3.0, 2.65, 2.0);
    }
}

module closure_coupon_rear() {
    difference() {
        union() {
            translate([
                -closure_coupon_w / 2, 0, 0
            ]) cube([
                closure_coupon_w,
                closure_coupon_wall_d,
                closure_coupon_shell_z
            ]);
            translate([0, case_h / 2, 0])
                closure_socket_y(
                    -closure_track_l / 2, -1, closure_coupon_shell_z, 1
                );
        }
        for (x = [-9, 9])
            translate([
                x, closure_coupon_wall_d / 2,
                closure_coupon_shell_z - 2.05
            ]) cylinder(d1 = 3.30, d2 = 3.10, h = 2.15);
    }
}

module closure_coupon_assembly() {
    // The installed rear coupon is flipped exactly like the production rear
    // cap: its local seam at z=8 lands on top of the front's 2.6 mm spacer.
    color("#3b91b5") closure_coupon_front();
    color("#405e6d")
        translate([0, 0, 2 * closure_coupon_shell_z + plate_t])
            rotate([180, 0, 0]) closure_coupon_rear();

    // Nominal key seated 0.2 mm before the blind stop. Local key Y spans the
    // shell axis; local key Z points outward from the coupon wall.
    key_start = -closure_track_l / 2
              + closure_track_l - closure_track_stop
              - closure_key_l - 0.20;
    color("#dcae52")
        multmatrix([
            [1, 0, 0, key_start],
            [0, 0, 1, 0],
            [0, 1, 0, closure_coupon_shell_z + plate_t / 2],
            [0, 0, 0, 1]
        ]) seam_draw_key();
}

module cable_keeper() {
    // Flat, replaceable keeper for the 16 mm display FFC. It clicks over the
    // two tapered posts and rests on their shoulders, 1.15 mm above the cable.
    difference() {
        rounded_prism([40, 10, 1.40], 2.5);
        for (x = [-ribbon_post_dx / 2, ribbon_post_dx / 2])
            translate([x, 0, -0.1]) cylinder(d = 6.55, h = 1.60);
    }
}

// ---------- Hardware ghosts / cable keepouts ----------
module hardware_ghosts(alpha = 0.75) {
    // Display lens/pan.
    color("#26343d", alpha)
        translate([0, display_y, front_skin])
            rounded_prism([display_w, display_h, 1.4], 6.5);
    color("#9ba6ab", alpha)
        translate([-1.49, display_y - 1.55, front_skin + 1.4])
            rounded_prism([166.2, 100.6, display_total_t - 1.4], 2.0);

    // Display conversion board/controller keepout.
    color("#357b52", alpha)
        box_at([25, display_y - 2, 10.3], [85, 56, 7.0]);

    // SSD faces the display; NU08 is the seam plane.
    color("#d5dbe0", alpha)
        box_at([ssd_cx, ssd_cy,
                nu08_plane_z - nu08_pcb_t / 2 - 1.2 - ssd_t / 2],
               [ssd_w, ssd_h, ssd_t]);
    color("#272b31", alpha)
        box_at([nu08_cx, nu08_cy, nu08_plane_z],
               [nu08_w, nu08_h, nu08_pcb_t]);

    // Pi and active cooler face the vented rear.
    color("#2e8b57", alpha)
        box_at([pi_cx, pi_cy, pi_plane_z], [pi_w, pi_h, pi_pcb_t]);
    color("#171b1e", alpha)
        box_at([pi_cx - 4, pi_cy + 1,
                pi_plane_z + pi_pcb_t / 2 + cooler_z / 2],
               [cooler_w, cooler_h, cooler_z]);

    // Ethernet/USB and USB bridge keepouts at the lower edge.
    color("#6f7d84", alpha)
        box_at([pi_cx + 3.0, pi_cy - pi_h / 2 - 3,
                pi_plane_z + 2.6], [57, 8, 12]);
    color("#17191c", alpha)
        box_at([io_cx, -63, nu08_plane_z + 5.5], [24, 26, 15]);

    // USB-C + HDMI face inward over the NU08; the rear hatch exposes them.
    color("#858e93", alpha)
        for (y = [-4.7, 8.7, 23.3])
            box_at([pi_cx - pi_w / 2 - 4.5, y,
                    pi_plane_z + 2.2], [12, y == 23.3 ? 13 : 9, 8]);

    // Display DSI bend corridor and NU08 PCIe FFC keepout.
    color("#f0d15f", 0.45)
        box_at([ribbon_gate_x, 34, 20], [34, 28, 20]);
    color("#c47e48", 0.45)
        box_at([0, 15, nu08_plane_z], [42, 22, 8]);
}

// ---------- Assembly preview ----------
closure_key_trailing = closure_track_l - closure_track_stop
                     - closure_key_l - 0.20;

module seam_draw_key_on_y(x_open, y_sign = 1, u_sign = 1) {
    seam_mid_z = front_depth + plate_t / 2;
    multmatrix([
        [u_sign, 0,      0,
         x_open + u_sign * closure_key_trailing],
        [0,      0, y_sign, y_sign * case_h / 2],
        [0,      1,      0, seam_mid_z],
        [0,      0,      0, 1]
    ]) seam_draw_key();
}

module seam_draw_key_on_x(x_sign = 1, y_open = 0, u_sign = 1) {
    seam_mid_z = front_depth + plate_t / 2;
    multmatrix([
        [0,      0, x_sign, x_sign * case_w / 2],
        [u_sign, 0,      0,
         y_open + u_sign * closure_key_trailing],
        [0,      1,      0, seam_mid_z],
        [0,      0,      0, 1]
    ]) seam_draw_key();
}

module io_cassette_preview() {
    color("#607985") io_bezel();
    color("#d5b565")
        translate([0, io_slider_lift,
                   io_hood_depth + io_spigot_depth])
            io_slider("open");
    color("#8199a4")
        translate([0, 0,
                   io_hood_depth + io_spigot_depth + io_slider_t])
            io_backer();
}

module installed_accessories() {
    // Replaceable natural-hole alignment pins.
    color("#e3b85b") {
        for (p = display_holes)
            translate([p[0], p[1], display_pocket_z + retainer_t + 1.0])
                rotate([180, 0, 0]) display_locator_pin();
        for (u = nu08_hole_u)
            for (v = nu08_hole_v) {
                p = nu08_world(u, v);
                translate([p[0], p[1], front_depth - 0.9]) nu08_snap_pin();
            }
    }

    // DSI keeper seated on the retainer's post shoulders.
    color("#d6dde1")
        translate([ribbon_gate_x, ribbon_gate_y,
                   display_pocket_z + retainer_t - 0.05 + 1.15])
            cable_keeper();

    // Back door and adjustable I/O cassette in rear-cap print coordinates,
    // followed
    // by the same installation transform as the rear cap itself.
    translate([0, 0, case_total_z]) rotate([180, 0, 0]) {
        color("#496372")
            translate([-57, -7, -2.4]) rear_service_door(true);
        translate([io_cx, case_h / 2 + io_hood_depth,
                       (port_notch_floor + rear_depth) / 2])
            rotate([90, 0, 0]) io_cassette_preview();
    }

    // Six mechanically captured, replaceable seam draw-keys.
    color("#bfc9ce") {
        seam_draw_key_on_y(-48 - closure_track_l / 2,  1,  1);
        seam_draw_key_on_y( 48 + closure_track_l / 2,  1, -1);
        seam_draw_key_on_y(-79 - closure_track_l / 2, -1,  1);
        seam_draw_key_on_y( 64 + closure_track_l / 2, -1, -1);
        seam_draw_key_on_x(-1, -closure_track_l / 2, 1);
        seam_draw_key_on_x( 1, -closure_track_l / 2, 1);
    }
}

module assembled(exploded = false) {
    e = exploded ? explode : 0;
    color("#1b2228") front_chassis();

    color("#88a9b7")
        translate([0, 0, display_pocket_z + e]) display_retainer();

    color("#9fb6c0")
        translate([0, 0, front_depth + 2 * e]) electronics_carrier();

    color("#354955")
        translate([0, 0, front_depth + plate_t + rear_depth + 3 * e])
            rotate([180, 0, 0]) rear_cap();

    if (show_hardware) hardware_ghosts(0.58);
    if (show_accessories && !exploded) installed_accessories();
}

// ---------- Output ----------
if (part == "front") {
    front_chassis();
} else if (part == "display_retainer") {
    display_retainer();
} else if (part == "carrier") {
    electronics_carrier();
} else if (part == "rear") {
    rear_cap();
} else if (part == "io_bezel") {
    io_bezel();
} else if (part == "io_slider_open") {
    io_slider("open");
} else if (part == "io_slider_fitted") {
    io_slider("fitted");
} else if (part == "io_slider_blank") {
    io_slider("blank");
} else if (part == "io_backer") {
    io_backer();
} else if (part == "io_wall_coupon") {
    io_wall_coupon();
} else if (part == "retainer_latch_wall") {
    retainer_latch_coupon_wall();
} else if (part == "retainer_latch_tab") {
    retainer_latch_coupon_tab();
} else if (part == "stack_spacers") {
    stack_spacing_blocks();
} else if (part == "rear_door") {
    rear_service_door(true);
} else if (part == "rear_door_closed") {
    rear_service_door(false);
} else if (part == "carrier_gauge") {
    carrier_alignment_gauge();
} else if (part == "seam_draw_key") {
    seam_draw_key();
} else if (part == "seam_draw_keys") {
    for (x = [0 : 27 : 135]) translate([x, 0, 0]) seam_draw_key();
} else if (part == "seam_draw_key_loose") {
    seam_draw_key(0.55);
} else if (part == "seam_draw_key_snug") {
    seam_draw_key(0.25);
} else if (part == "closure_coupon_front") {
    closure_coupon_front();
} else if (part == "closure_coupon_rear") {
    closure_coupon_rear();
} else if (part == "closure_coupon_assembly") {
    closure_coupon_assembly();
} else if (part == "display_pins") {
    pin_sprue("display");
} else if (part == "nu08_pins") {
    pin_sprue("nu08");
} else if (part == "pin_coupon") {
    pin_fit_coupon();
} else if (part == "cable_keeper") {
    cable_keeper();
} else if (part == "hardware") {
    hardware_ghosts(1.0);
} else if (part == "section") {
    intersection() {
        assembled(false);
        box_at([0, 0, case_total_z / 2], [case_w + 30, 1.2, case_total_z + 30]);
    }
} else if (part == "exploded") {
    assembled(true);
} else {
    assembled(false);
}
