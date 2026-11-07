# PiRouter SnapShell v2 (archived)

This document records the superseded v2 design for this hardware stack. Its
source and generated exports are not distributed here. Use `README.md` and
`pirouter_case_v3.scad` for the maintained model.

The archived design targeted:

- original Raspberry Pi 7-inch Touch Display (800 × 480)
- Raspberry Pi 5
- 52Pi NU08 / EP-0213 / N12 storage board, 115 × 95 mm
- 2.5-inch SATA SSD, allowing a 9.5 mm body
- Pi 5 active cooler, using a conservative 58 × 45 × 19 mm keepout

Do not print the original or v2 revisions; both have been superseded by v3.

## What changed

The display and Pi/NU08 assembly are now retained independently. That matters
because the NU08 occupies the Pi underside and cannot share the display's Pi
standoffs.

From front to rear, v2 is:

1. support-free front chassis and glass pocket
2. separate skeletal display retainer on the display's official M3 pattern
3. SSD-facing cavity with a lower-centre display-tail escape and upper DSI lane
4. full-width NU08 carrier/mid-frame on four replaceable natural-hole pins
5. Pi/cooler cavity and back-down rear cap
6. removable lower I/O insert, rear plug door, and six external seam clips

The case itself uses no screws. Keep the normal screws/standoffs that join the
SSD, NU08, Pi, cooler, and any parts of the factory hardware stack; printed
pins replace only unused enclosure/mounting-hole fasteners.

All large apertures are U-notches open to a mating edge. There are no long roofs
over the Ethernet/USB/bridge bay, no floating display shelves, and no broad
internal bridges.

## Print these small checks first

These checks are intentional because 52Pi publishes the 115 × 95 mm outline but
not a production hole-coordinate drawing, and the supplied rigid USB bridge is
not dimensioned.

1. `carrier_alignment_gauge.stl` — a 1 mm outside-only NU08 frame. Confirm all
   four holes without forcing it over components. If they miss, edit
   `nu08_hole_u` and `nu08_hole_v` before printing the carrier.
2. `pin_fit_coupon.stl` — top-row holes are 2.40, 2.55, 2.70, and 2.85 mm;
   bottom-row holes are 3.20, 3.35, and 3.50 mm. Raised tick count identifies
   each size. Choose the smallest free-moving socket and a natural-hole peg
   that compresses without whitening.
3. `clip_fit_coupon.stl` plus one `seam_clip.stl` — the nominal rail/clip fit
   has 0.20 mm interference. The clip should seat with thumb pressure and stay
   put without a stress-white hinge.
4. `bottom_io_open.stl` — use this broad insert for the first assembly. The
   `bottom_io_nominal.stl` port groups are based on official Pi geometry plus
   image-derived NU08 placement; use them only after comparing against the
   physical bridge and plugs.

## Printable parts

- `front_chassis.stl` — display bezel, pocket, front cavity, tail clearance,
  lower bridge bay, shell dowels, and clip rails
- `display_retainer.stl` — official offset M3 alignment pattern, lower flex-tail
  opening, four clamp towers, and DSI keeper posts
- `electronics_carrier.stl` — full seam-filling mid-frame with NU08 hole pads,
  PCB stand-off collars, board/SSD clearance, and cable openings
- `rear_cap.stl` — vented cooler cavity, full-height lower I/O bay, straight
  rear access to the inward-facing USB-C/HDMI row, sockets, and clip rails
- `bottom_io_open.stl` / `bottom_io_nominal.stl` — interchangeable press-fit
  lower panels
- `rear_service_door_cabled.stl` — three rim-open cable exits and three long
  in-layer snap beams
- `rear_service_door_closed.stl` — closed face and four long in-layer snap beams
- `seam_clip.stl` — print six after fitting the coupon
- `display_locator_pins.stl` — four M3-thread-minor locator noses on a sprue
- `nu08_snap_pins.stl` — four split M2.5-class natural-hole pins on a sprue
- `display_cable_keeper.stl` — removable 18 mm DSI gate keeper
- `carrier_alignment_gauge.stl`, `pin_fit_coupon.stl`, and
  `clip_fit_coupon.stl` — calibration parts

## Dimensions that remain physical checks

The nominal exterior is 208 × 140 × 69.6 mm; seam rails reach about
209.7 × 141.7 mm. A printer needs at least roughly 212 × 145 mm of genuinely
usable bed area.

Before the two large shells, check:

- the assembled SSD-facing surface to cooler-top depth
- the exact SSD/enclosure thickness (`ssd_t` defaults to 9.5 mm)
- NU08-to-Pi spacing (`pi_standoff` defaults to 11 mm centre-to-centre)
- cooler height (`cooler_z` defaults to 19 mm)
- rigid USB bridge protrusion and which blue USB socket it occupies
- USB-C plug body and desired bend direction

The archived model left about 3 mm behind the conservative cooler ghost. If
your measured stack is taller, increase `rear_depth`; do not scale the STL,
because scaling moves the official display and port datums.

## Print settings and orientation

- material: PETG recommended for pins, clips, keeper, and service door; PETG or
  PLA+ for the large shells
- nozzle/layer: 0.4 mm / 0.20 mm
- walls: 4 perimeters
- top/bottom: 5 layers
- infill: 20–25% gyroid or cubic
- supports: none
- front chassis: glass/exterior face on the bed
- display retainer: flat skeletal plate on the bed, towers upward
- carrier: flat front face on the bed, board collars upward
- rear cap: exterior rear face on the bed, open seam upward
- I/O inserts and doors: exterior face on the bed
- pins: upright as exported; break the 0.45 mm sprue after printing
- seam clips: end-up as exported with a 3–5 mm brim, so the C flexure remains
  continuous within each layer

Use elephant-foot compensation for the carrier and press-fit inserts. Never
force a pin into a PCB or threaded display hole; reprint the tiny pin/coupon
before risking hardware.

## Assembly

1. Fully assemble and test the Pi, cooler, NU08, SSD, pogo connection, PCIe FFC,
   rigid USB bridge, DSI cable, and display power jumpers outside the case.
2. Put the front chassis exterior-down on a clean microfiber cloth. Seat the
   display tail-down toward the lower I/O chin.
3. Feed the display locator pins through the retainer from the tower side, with
   their tapered 2.30 mm noses pointing toward the display. The noses locate in
   the M3 thread minors; they do not thread or preload the glass.
4. Add soft 1.0–1.5 mm EVA/felt only between the retainer and safe metal-frame
   areas, and 0.8 mm EVA on the four tower tips. Use only enough compression to
   remove rattle. No hard point should bear on glass, the LCD, or a component.
5. Keep the panel's built-in flex in the lower-centre retainer opening. Route
   the external DSI ribbon around the upper edge of the SSD, through the open
   carrier centre, and under the 18 mm keeper. Preserve at least an 8 mm straight
   lead at each ZIF and about a 5 mm inside bend radius.
6. Push the four NU08 pins through the carrier from its display/SSD side. Seat
   the assembled NU08 with the SSD toward the display and the Pi/cooler toward
   the rear. The PCB rests on the four 0.30 mm collars; the split tips catch the
   unused natural holes.
7. Fit the broad lower I/O insert into the rear notch and choose the cabled or
   closed rear service door. The cabled door supports USB-C plus both HDMI leads;
   the door can also remain off during cable setup.
8. Bring the rear cap onto the four tapered alignment dowels. The carrier's
   2.6 mm outer band is captured between the shells and fills the seam.
9. Slide the six clips over the paired external rails. Work around the perimeter
   instead of forcing one corner fully closed first.

To service it, unplug power, slide the six seam clips off, release the service
door beams from inside if needed, and lift the rear cap straight off the dowels.
The sacrificial pins, door, keeper, I/O panel, and clips can all be reprinted
without replacing a large shell.

## Export and validation

The current `export.sh` generates v3 only. Follow `README.md` for its export
and validation instructions; the old v2 exports cannot be reproduced from
the sources distributed here.

## Geometry sources

- [Original 7-inch display mechanical drawing](https://pip-assets.raspberrypi.com/categories/647-raspberry-pi-touch-display/documents/RP-008246-DS-1-7-inch-display-mechanical-drawing.pdf?disposition=inline)
- [Raspberry Pi display enclosure guidance](https://pip-assets.raspberrypi.com/categories/1259-audio-camera-and-display/documents/RP-003473-WP/Touch-Display-Enclosure-Mechanical-Design)
- [Raspberry Pi 5 mechanical drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf)
- [52Pi NU08 / EP-0213 documentation](https://wiki.52pi.com/index.php?title=EP-0213)
- [Official display/camera FFC reference drawing](https://pip-assets.raspberrypi.com/categories/903-flexible-printed-circuit-fpc-cable-for-display-and-camera/documents/RP-008145-DS-1-mini-display-cable-200mm-reference-drawing.pdf)
