# PiRouter SnapShell v3

This is the screwless enclosure for this hardware stack:

- original Raspberry Pi 7-inch Touch Display (800 × 480)
- Raspberry Pi 5 with active cooler
- 52Pi NU08 / EP-0213 / N12 storage board, 115 × 95 mm
- 2.5-inch SATA SSD, allowing a 9.5 mm body
- the supplied rigid black dual-USB bridge

The current editable model is `pirouter_case_v3.scad`; generate production
exports in `stl-v3/` with `export.sh`. Generated files are not distributed in
the source repository. Do not print the superseded original or v2 revisions.

The enclosure adds no screws. Keep the ordinary screws and standoffs that are
part of the Pi/NU08/SSD/cooler assembly. Replaceable printed pins use otherwise
unused display and NU08 mounting holes; tapered shell dowels align the three
case layers; six external sliding draw-keys positively lock the seam.

## Design rationale

v2 modeled the Pi on the NU08's right end. The Pi belongs on the **left** end
in the usable landscape orientation. The Ethernet/USB bank is about 59 mm
left of the old opening. v3 corrects the
hardware coordinates rather than hiding the error with a huge hole.

The supported orientation is landscape:

- NU08 long dimension runs left/right.
- Pi is at the NU08's left end.
- Ethernet, both USB stacks, and the rigid bridge face the lower long edge.
- SSD faces the display.
- USB-C and micro-HDMI face the left side and are reached through the rear
  service door.

Portrait orientation is unsupported: it drives the
rigid bridge into a solid short wall. A raised arrow on the carrier points to
the only valid port-facing edge.

Another v2 fit issue was its lower-right display-retainer tower.
It occupied the SSD/NU08 envelope with only about 3.3 mm nominal clearance.
All four towers are gone. The new retainer is a 2.4 mm skeletal plate with four
long, in-layer flexures that latch directly into shallow front-shell pockets.

The old front-half port opening is also gone. The
bridge starts at the electronics seam, so the opening now exists only in the
rear cap.

## What to print first

Do not begin with either large shell. Print these inexpensive checks first:

1. `closure_coupon_front.stl`, `closure_coupon_rear.stl`, and one each of
   `seam_draw_key_loose.stl`, `seam_draw_key_nominal.stl`, and
   `seam_draw_key_snug.stl`. Print the two coupon halves seam-up, just like the
   large shells, and print each key with its broad wall-facing side flat on the
   bed. Flip the rear coupon onto the front coupon's two pins; its integral
   2.6 mm spacer reproduces the real carrier seam. Start with the nominal key.
   It should slide to the blind stop, click into the release window, and stop
   the halves pulling apart. Press the visible tongue inward to withdraw it.
   Use loose only if nominal binds, or snug only if nominal rattles.
2. `retainer_latch_coupon_wall.stl` and
   `retainer_latch_coupon_tab.stl`. Put the tab on the coupon foot, ramp toward
   the wall, and press it down into the pocket. It should click with thumb
   pressure and release when the 20 mm beam is pushed inward.
3. `io_wall_coupon.stl`, `io_bezel_hood.stl`,
   `io_slider_universal.stl`, and `io_backer.stl`. The hood's spigot should
   enter the U-notch without filing. Put the universal slider over the four
   tapered pins with its small edge notch facing the open/seam edge, then press
   on the backer.
4. If the earlier v2 pin coupon worked, reuse that setting; the pin mating
   geometry is unchanged. Otherwise print `pin_fit_coupon.stl`. The old v2
   rail/clip coupon does not test the new closure and should not be reused.

After those checks pass, print the two structural internals:

5. `display_retainer_low_profile.stl`
6. `electronics_carrier_photo_corrected.stl`
7. `stack_spacing_blocks_13p1mm.stl`

Break the four 13.1 mm spacing blocks off their thin sprue. With the actual
display located in the retainer and the complete Pi/NU08/SSD stack located on
the carrier, place the blocks only on safe outer rails between the retainer's
rear face and the carrier's front face. This reproduces the installed spacing
without a shell. Confirm that:

- the SSD bracket and screw heads clear the flat retainer;
- the orange display flex has an unpinched lower-centre exit;
- the DSI ribbon can take the upper route without a hard crease;
- the rigid bridge points toward the carrier arrow/I/O edge;
- no board is being forced onto a printed post or spoke.

Only after that dry fit should the two large files be printed:

8. `front_chassis_v3.stl`
9. `rear_cap_compact.stl`

Finish with the selected service door, six copies of the draw-key size that
passed the coupon, locator pins, and cable keeper. Start with
`io_slider_universal.stl`; use the fitted mask only after the universal opening
has proved the final position.

## Adjustable motherboard-style I/O cassette

The shell contains a 90 mm rim-open bay centered on the corrected port bank.
The removable cassette consists of:

- `io_bezel_hood.stl` — exterior frame, 8 mm protective bridge hood, notch
  spigot, and four tapered backer pins
- `io_slider_universal.stl` — one 64 × 22 mm connector opening plus a 30 mm
  U-leg that stays open to the carrier seam for the rigid bridge
- `io_slider_fitted.stl` — separate nominal RJ45/USB openings, for cosmetic
  use only after the physical stack is proven; it retains the same bridge
  U-leg
- `io_slider_blank.stl` — editable/drillable small plate for a future exact
  cosmetic mask
- `io_backer.stl` — inside clamp ring

The masks physically float about ±8 mm horizontally and roughly ±2 mm
vertically; the clear frame determines how much of that vertical travel is
useful for a particular mask. At the nominal setting all four bezel pins sit
near the centres of the mask slots. To adjust, remove or ease back the inner
ring, move the mask while watching real connector clearance, and press the ring
back onto the tapered pins. Never use the mask to push the PCB or bridge into
position.

Keep at least 1.5 mm of connector metal proud of the final plastic face so a
USB or Ethernet plug cannot bottom against the hood. The single universal
opening deliberately avoids the RJ45 latch and LEDs. The fitted mask is not a
fit-test substitute.

## Positive-lock seam closure

The v2 bead rails and C-clips are retired. They relied on a small friction fit
and had no geometry that prevented a loose clip sliding off. Every v3 shell
half now has six external, blind-ended dovetail sockets. Each flat draw-key has
thick heads captured 4.5 mm inside both shells and a narrow waist across the
2.6 mm carrier. Once inserted, it cannot pull out with either shell or peel
away from the wall; its only removal path is back through the socket entrance.

Any earlier v3 shell export that still has a thin bead rail is obsolete. The
draw-key sockets are integral to both large shells, so refresh and reprint both
`front_chassis_v3.stl` and `rear_cap_compact.stl`; new keys cannot retrofit the
old rail geometry.

A shallow integral tooth clicks into the release window near the entrance.
For removal, unplug the router, press that tongue toward the case with a
fingernail or 2 mm flat tool, and pull the small tab. Do not pry the outer
socket flange. The 0.10 mm draw surfaces take up seam play during the last few
millimetres of insertion.

All six sockets are 24 mm long. The two alongside the I/O bay sit at
`x=-79 mm` and `x=+64 mm`, leaving clearance for the floating cassette/backer.
All socket geometry is outside the enclosure wall and cannot touch the NU08,
carrier spokes, display ribbon, or SSD. The socket cavity opens toward the seam
and one end, while its only transition is 45 degrees, so the shells still
print without support.

Print six identical keys after choosing the coupon fit:

- loose: 0.55 mm nominal groove clearance
- nominal: 0.40 mm nominal groove clearance
- snug: 0.25 mm nominal groove clearance

PETG is preferred for the replaceable keys. If using PLA+, avoid repeatedly
bending the latch tongue farther than needed to clear the window.

## Size and clearance budget

The main body is 204 × 128 × 62.6 mm: 26 mm front cavity, 2.6 mm captured
carrier, and 34 mm rear cavity. This removes 4 mm of width, 12 mm of height, and
7 mm of depth from v2. The complete maximum footprint is about 212.3 × 140.2 mm
when the external draw-key housings and 8 mm connector hood are included. The
main rectangular body itself remains 204 × 128 mm.

The front-depth model leaves about 4.4 mm between the conservative display
controller keepout and the 9.5 mm SSD body, and 13.1 mm between the retainer
rear face and carrier front face. The rear model allows a conservative 19 mm
cooler keepout with about 2 mm to the rear exterior.

Those margins still depend on the physical SSD bracket/screw underhang and
cooler. If the dry fit or a direct measurement is taller, increase
`front_depth` or `rear_depth` in the SCAD and re-export. Do not scale an STL;
scaling moves the official display holes, board holes, closure sockets, and
port datums.

## Print settings and orientation

- material: PETG strongly recommended for retainer, latch coupon, pins,
  draw-keys, I/O backer, and service-door catches; PETG or PLA+ for the shells
- nozzle/layer: 0.4 mm / 0.20 mm
- walls: 4 perimeters
- top/bottom: 5 layers
- infill: 20–25% gyroid or cubic
- supports: none
- front chassis: exterior/touch face on the bed
- rear cap: exterior rear face on the bed, open seam upward
- display retainer and carrier: large flat face on the bed
- I/O hood: outward rim face on the bed
- masks, backer, doors, keeper, and coupons: flat face on the bed
- locator pins: upright as exported; break the 0.45 mm sprue after printing
- seam draw-keys: broad, flat wall-facing side on the bed; no brim or support

Use elephant-foot compensation on the carrier, I/O cassette, and coupons.
Never force a printed pin into a PCB or the display's threaded hole.

## Assembly

1. Fully assemble and test the Pi, cooler, NU08, SSD, PCIe ribbon, rigid USB
   bridge, DSI cable, and display power wiring outside the enclosure.
2. Put the front chassis exterior-down on a clean cloth. Seat the display with
   its built-in flex at the lower-centre exit.
3. Install the four display locator pins through the retainer. Their tapered
   2.30 mm noses locate in the display's thread minors; they do not thread or
   preload the glass. Use soft 1.0–1.5 mm EVA only on safe metal-frame areas.
4. Lower the retainer evenly. Its four ramps flex inward and click into the
   shallow side pockets. Verify every latch through the open rear; no tower or
   hard plastic should touch the SSD volume.
5. Route the DSI ribbon through open space, keeping at least an 8 mm straight
   lead at each ZIF and roughly a 6 mm inside bend radius. Fit the separate
   keeper without pinching the cable.
6. Push the four NU08 pins through the carrier from the display/SSD side. Seat
   the complete module with SSD toward the display, cooler toward the rear,
   Pi on the left, and the carrier arrow pointing toward Ethernet/USB.
7. Insert the I/O hood from outside the rear U-notch. Set the universal mask on
   its pins from inside and press on the backer. Fit the cabled or closed rear
   service door.
8. Bring the rear cap straight onto the six tapered dowels. The 2.6 mm carrier
   band is captured between both shells.
9. Slide six identical draw-keys into the corner-facing socket entrances until
   each tooth clicks into its release window. Work around the perimeter; never
   use a key to pull misaligned shells together.

For service, unplug power, press each key tongue inward through its window,
pull the keys by their small tabs, and remove the rear cap. The retainer
flexures, I/O mask, backer, door, pins, keeper, and draw-keys are all
replaceable without reprinting a large shell.

## Export and validation

From `enclosure/`:

```bash
./export.sh
```

The export script generates every v3 binary STL and runs `validate_stl.py`.
Every production STL must report one connected shell, zero boundary edges,
zero non-manifold edges, and zero degenerate triangles.

After exporting, generate the optional `previews/v3_parts.png` overview with
`python3 render_preview.py`. This requires Python with Pillow and NumPy.

In OpenSCAD, select `part="assembly"`, `part="exploded"`, or
`part="section"` for inspection. Individual part names are listed at the top
of `pirouter_case_v3.scad`.

## Geometry references

- [Original 7-inch display mechanical drawing](https://pip-assets.raspberrypi.com/categories/647-raspberry-pi-touch-display/documents/RP-008246-DS-1-7-inch-display-mechanical-drawing.pdf?disposition=inline)
- [Raspberry Pi display enclosure guidance](https://pip-assets.raspberrypi.com/categories/1259-audio-camera-and-display/documents/RP-003473-WP/Touch-Display-Enclosure-Mechanical-Design)
- [Raspberry Pi 5 mechanical drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf)
- [52Pi NU08 / EP-0213 documentation](https://wiki.52pi.com/index.php?title=EP-0213)
- [Official display/camera FFC reference drawing](https://pip-assets.raspberrypi.com/categories/903-flexible-printed-circuit-fpc-cable-for-display-and-camera/documents/RP-008145-DS-1-mini-display-cable-200mm-reference-drawing.pdf)
