/*
 * SUPERSEDED PROTOTYPE — DO NOT PRINT.
 * Use pirouter_case_v3.scad and README.md. This first revision does not fit the
 * complete NU08/SSD stack and does not have measured port/ribbon geometry.
 *
 * PiRouter SnapShell
 * Parametric, screw-less enclosure for:
 *   - Raspberry Pi 5
 *   - original official Raspberry Pi 7-inch Touch Display (800x480)
 *   - 52Pi NU08 / EP-0213 bottom storage board
 *   - 2.5-inch SSD
 *
 * Designed for FDM printing. The case halves use six serviceable cantilever
 * snaps; the display is captured between the front lip and padded ledges in
 * the rear shell. Existing fasteners in the Pi/display/NU08 hardware stack
 * remain in use; the printed enclosure itself uses no screws.
 *
 * Export with, for example:
 *   openscad -o stl/front_bezel.stl -D 'part="front"' pirouter_case.scad
 *
 * Units: millimetres.
 */

$fn = $preview ? 36 : 72;

// ---------- Select an output ----------
part = "assembly"; // assembly, front, rear, foot, feet, coupon
explode = 4;       // assembly-only separation

// ---------- Printer / material tuning ----------
fit = 0.30;             // XY clearance per mating side; start at 0.30 for PLA
elephant_relief = 0.25; // relief at mating-edge first layers
wall = 2.40;            // rear-shell wall
back = 2.60;            // rear-shell back
front_skin = 3.20;      // front face thickness
snap_t = 1.35;          // cantilever thickness; PETG: 1.2-1.35, PLA+: 1.35-1.5
snap_w = 13.0;
snap_reach = 9.20;
snap_bump = 0.75;

// ---------- Hardware envelope ----------
// Original official Touch Display published outer size: 192.96 x 110.76.
display_w = 192.96;
display_h = 110.76;
display_edge_t = 8.20;  // tune after measuring the perimeter of your unit
glass_overlap = 2.00;   // overlap on each edge; never overlaps active pixels

// The rear depth accommodates the Pi 5 + NU08 + a 7/9.5 mm 2.5-inch SSD.
// Measure from the front glass to the rearmost cable/component before printing.
stack_depth = 37.0;
rear_depth = max(43.0, stack_depth + 4.0);

frame_border = 4.60;
outer_w = display_w + 2 * frame_border;
outer_h = display_h + 2 * frame_border;
corner_r = 5.50;

display_pocket_w = display_w + 2 * fit;
display_pocket_h = display_h + 2 * fit;
screen_open_w = display_w - 2 * glass_overlap;
screen_open_h = display_h - 2 * glass_overlap;

rim_t = 1.55;
rim_h = 11.40;
rim_outer_w = display_pocket_w + 2 * rim_t;
rim_outer_h = display_pocket_h + 2 * rim_t;

// Rear ledges sit against small foam/felt pads on the display metal frame.
// Positive clamp_preload compresses the pads when the shell snaps home.
pad_t = 0.8;
clamp_preload = 0.35;
retainer_z = rear_depth - display_edge_t - pad_t + clamp_preload;

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
        rounded_rect_2d(inner, max(0.4, r - (outer[0] - inner[0]) / 2));
    }
}

// ---------- Front capture frame ----------
module front_rim_without_snap_gaps() {
    difference() {
        translate([0, 0, front_skin - 0.01])
            linear_extrude(height = rim_h + 0.01)
                ring_2d([rim_outer_w, rim_outer_h],
                        [display_pocket_w, display_pocket_h], 2.2);

        // Isolate each cantilever from the surrounding registration rim.
        for (x = [-52, 52])
            for (y_sign = [-1, 1])
                box_at([x, y_sign * rim_outer_h / 2, front_skin + rim_h / 2],
                       [snap_w + 2.2, 5.0, rim_h + 1]);
        for (x_sign = [-1, 1])
            box_at([x_sign * rim_outer_w / 2, 0, front_skin + rim_h / 2],
                   [5.0, snap_w + 2.2, rim_h + 1]);
    }
}

module snap_tab_y(x, y_sign) {
    y0 = y_sign * (display_pocket_h / 2 + snap_t / 2);
    // Flexible blade, anchored only at the front frame.
    box_at([x, y0, front_skin - 0.15 + (snap_reach + 0.15) / 2],
           [snap_w, snap_t, snap_reach + 0.15]);

    // Lead-in wedge. Its square shoulder is reachable through the release slot.
    hull() {
        box_at([x, y0,
                front_skin + snap_reach - 1.10], [snap_w, snap_t, 0.25]);
        box_at([x, y0 + y_sign * snap_bump / 2,
                front_skin + snap_reach - 0.35],
               [snap_w, snap_t + snap_bump, 0.35]);
    }
}

module snap_tab_x(x_sign, y) {
    x0 = x_sign * (display_pocket_w / 2 + snap_t / 2);
    box_at([x0, y, front_skin - 0.15 + (snap_reach + 0.15) / 2],
           [snap_t, snap_w, snap_reach + 0.15]);
    hull() {
        box_at([x0, y, front_skin + snap_reach - 1.10],
               [snap_t, snap_w, 0.25]);
        box_at([x0 + x_sign * snap_bump / 2, y,
                front_skin + snap_reach - 0.35],
               [snap_t + snap_bump, snap_w, 0.35]);
    }
}

module front_bezel() {
    union() {
        difference() {
            rounded_prism([outer_w, outer_h, front_skin], corner_r);
            // Slight rearward flare keeps the first layer away from the glass.
            translate([0, 0, -0.1])
                linear_extrude(height = front_skin + 0.2)
                    rounded_rect_2d([screen_open_w, screen_open_h], 1.8);
        }
        front_rim_without_snap_gaps();
        for (x = [-52, 52]) {
            snap_tab_y(x, -1);
            snap_tab_y(x, 1);
        }
        snap_tab_x(-1, 0);
        snap_tab_x(1, 0);
    }
}

// ---------- Rear shell ----------
module snap_release_windows() {
    window_z = rear_depth - snap_reach + 0.65;
    for (x = [-52, 52])
        for (y_sign = [-1, 1])
            box_at([x, y_sign * outer_h / 2, window_z],
                   [snap_w + 1.6, wall + 2, 3.2]);
    for (x_sign = [-1, 1])
        box_at([x_sign * outer_w / 2, 0, window_z],
               [wall + 2, snap_w + 1.6, 3.2]);
}

module io_windows() {
    // Right edge: Ethernet + four USB ports. Broad because the display's Pi
    // mounting permits more than one stack orientation.
    box_at([outer_w / 2, 7, 20.0], [wall + 3, 73, 19.0]);

    // Bottom edge: USB-C power, dual micro-HDMI, and cable exit.
    box_at([8, -outer_h / 2, 18.0], [112, wall + 3, 16.0]);

    // Left edge: microSD service access and optional FFC/cable exit.
    box_at([-outer_w / 2, 5, 17.0], [wall + 3, 34, 11.0]);
}

module back_vent_slots() {
    // Slots stay short enough to bridge cleanly and leave a strong spine.
    for (x = [-72, -54, -36, 36, 54, 72])
        for (y = [-36, -24, -12, 0, 12, 24, 36])
            translate([x, y, -0.1])
                linear_extrude(height = back + 0.2)
                    rounded_rect_2d([11.0, 3.2], 1.6);

    // Larger centre exhaust above the Pi 5 cooler; guarded by 3 mm ribs.
    for (y = [-34 : 8.5 : 34])
        translate([0, y, -0.1])
            linear_extrude(height = back + 0.2)
                rounded_rect_2d([46, 3.0], 1.5);
}

module retainer_ledges() {
    ledge_z0 = retainer_z - 2.4;
    ledge_h = 2.4;
    ledge_w = 20;
    ledge_d = 14;
    ledge_x = (outer_w - 2 * wall) / 2 - ledge_w / 2 + 0.8;
    ledge_y = (outer_h - 2 * wall) / 2 - ledge_d / 2 + 0.8;
    // Pads land at the four outer corners of the display metal frame, clear of
    // the centrally mounted controller, Pi, NU08 board, and SSD.
    for (x = [-1, 1])
        for (y = [-1, 1])
            translate([x * ledge_x, y * ledge_y, ledge_z0])
                rounded_prism([ledge_w, ledge_d, ledge_h], 2.0);
}

module stand_socket_holes() {
    // Paired through-holes accept the barbed prongs on each removable foot.
    for (foot_x = [-62, 62])
        for (prong_x = [-4.3, 4.3])
            translate([foot_x + prong_x, -21, -0.1])
                linear_extrude(height = back + 0.2)
                    rounded_rect_2d([2.8 + 2 * fit, 8.2 + 2 * fit], 1.0);
}

module rear_shell() {
    difference() {
        union() {
            difference() {
                rounded_prism([outer_w, outer_h, rear_depth], corner_r);
                // Open front and hollow the shell. Extra height guarantees a clean opening.
                translate([0, 0, back])
                    linear_extrude(height = rear_depth - back + 0.2)
                        rounded_rect_2d([outer_w - 2 * wall,
                                         outer_h - 2 * wall],
                                        max(1.0, corner_r - wall));
            }
            retainer_ledges();
        }
        snap_release_windows();
        io_windows();
        back_vent_slots();
        stand_socket_holes();

        // Mating-edge relief avoids elephant-foot interference.
        translate([0, 0, rear_depth - 1.0])
            linear_extrude(height = 1.1)
                difference() {
                    rounded_rect_2d([outer_w + 0.2, outer_h + 0.2], corner_r);
                    rounded_rect_2d([outer_w - 2 * (wall + elephant_relief),
                                     outer_h - 2 * (wall + elephant_relief)],
                                    max(1.0, corner_r - wall));
                }
    }
}

// ---------- Clip-in feet ----------
// Modelled in their installed orientation against the exterior rear face.
module foot_mounted() {
    union() {
        // Angled kickstand beam, from the rear mount to a point below and
        // behind the enclosure. The resulting screen lean is about 14 degrees.
        hull() {
            box_at([0, -21, -3.0], [16, 12, 3.2]);
            box_at([0, -64, -28.0], [19, 13, 4.0]);
        }
        // Mounting pad joins the angled beam to the prongs.
        box_at([0, -21, -0.7], [16, 12, 2.0]);
        box_at([0, -67, -30.5], [28, 18, 3.0]);

        // Twin prongs pass through the shell back and catch on its inner face.
        for (x_sign = [-1, 1]) {
            box_at([x_sign * 4.3, -21, 2.4], [2.1, 7.5, 5.2]);
            hull() {
                box_at([x_sign * 4.3, -21, 4.1], [2.1, 7.5, 0.4]);
                box_at([x_sign * 4.65, -21, 5.0], [2.8, 7.5, 0.4]);
            }
        }
    }
}

module foot_printable() {
    // Put the broad side of the kickstand on the bed.
    translate([31, 67, 14]) rotate([0, -90, 0]) foot_mounted();
}

// Small, fast print for checking clearance and snap flexibility before the case.
module fit_coupon() {
    difference() {
        union() {
            cube([36, 20, 3]);
            translate([18, 4, 2.85]) cube([snap_w, snap_t, snap_reach + 0.15]);
            hull() {
                box_at([18 + snap_w / 2, 4 + snap_t / 2,
                        3 + snap_reach - 1.10], [snap_w, snap_t, 0.25]);
                box_at([18 + snap_w / 2, 4 + snap_t / 2 + snap_bump / 2,
                        3 + snap_reach - 0.35],
                       [snap_w, snap_t + snap_bump, 0.35]);
            }
        }
        box_at([18 + snap_w / 2, 19.0, 3 + snap_reach - 0.45],
               [snap_w + 1.6, 3, 3.2]);
    }
}

// ---------- Output layout ----------
if (part == "front") {
    front_bezel();
} else if (part == "rear") {
    // Print with the flat rear wall on the bed (as modelled).
    rear_shell();
} else if (part == "foot") {
    foot_printable();
} else if (part == "feet") {
    translate([-12, 0, 0]) foot_printable();
    translate([12, 0, 0]) foot_printable();
} else if (part == "coupon") {
    fit_coupon();
} else {
    // Exploded assembly: glass/front at z=0, rear shell behind it.
    color("#20252c") front_bezel();
    color("#506878", 0.85)
        translate([0, 0, front_skin + rear_depth + explode])
            rotate([180, 0, 0]) rear_shell();
    color("#d9e1e8") {
        translate([-62, 0, front_skin + rear_depth + explode])
            rotate([180, 0, 0]) foot_mounted();
        translate([62, 0, front_skin + rear_depth + explode])
            rotate([180, 0, 0]) foot_mounted();
    }
}
