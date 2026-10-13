// VL53L0X carrier for Bender's LEFT eye.
//
// WHY HERE, AND NOT THE SEAM BETWEEN THE EYES
// -------------------------------------------
// The seam cannot take the board. Measured from the DroidFactory meshes on the
// NAS, and confirmed against the built head:
//
//   eyeball sphere diameter   89.43 mm   (119.240 mm stock, printed at 75%)
//   eye centre spacing        92.88 mm   (owner's calipers)
//   GAP BETWEEN THE EYEBALLS   3.45 mm
//
// The board is 11 mm wide. An 11 mm slot first appears 17.98 mm behind the eye
// centre plane, and the eyeballs stop existing at 23.29 mm, so there is a 3 mm
// window. A sensor in that window still has to see forward through the 3.45 mm
// throat at the equator. The VL53L0X's own emitter-to-receiver span is 4.4 mm,
// wider than the throat. Nothing usable gets out, and the sphere surfaces sit
// in the beam feeding it crosstalk.
//
// The owner's own custom eye solved it instead. BenderEye-Cust.FCStd hollows
// the stock eyeball, flat-cuts the back 31.06 mm behind centre (23.29 mm at
// 75%), and cuts a 22 x 22 mm square hole straight through the front on the
// eye axis. Printed, that hole measures 16.7 mm square. It is the twin of the
// one the Camera Module 3 looks through in the right eye, and it is already
// aimed exactly where a child stands.
//
// AXES
//   +z is forward, toward the child.
//   z = 0 is the flange's back face, which is also where the board's front
//   face lands. The die then looks straight up the bore.
//
// ORIENTATION AT ASSEMBLY
//   The snout is square, so the part drops into the square aperture in any of
//   four rotations. Pick whichever sends the header and its cable towards the
//   open back cut of the eye shell. Nothing in this file depends on it.
//
// FITTING
//   1. Slide the snout into the aperture from INSIDE the hollow eye shell.
//   2. The flange stops it and bears on the inner face of the eye's front wall.
//      That face is spherical and this flange is flat, so a gap of about 1 mm
//      is expected at the corners. Glue fills it.
//   3. Drop the board into the rails, face forward, die towards the bore.
//   4. Slide along the rails until the die is centred in the aperture. Sight
//      it through the front of the eye. The tolerance is wide: the beam is
//      7.50 mm across at the snout mouth inside a 14 mm bore, so the die may
//      sit over 3 mm off centre before anything clips.
//   5. Lock it. Two M3 screws if `posts` is true and the pitch below is right,
//      otherwise a dab of glue on the rails.
//
// NO WINDOW. Do not cover the aperture with acrylic or film. A cover needs the
// crosstalk calibration that the Adafruit CircuitPython driver does not expose.
// Print in matt BLACK. A reflective bore returns a short, STABLE reading with
// nobody there, and that latches the presence gate open while looking exactly
// like a healthy sensor. After fitting, set `presence_min_mm` from
// `venv/bin/python scripts/probe_vl53l0x.py --baseline`.

$fn = 64;

// ---------------------------------------------------------------------------
// Eye. MEASURED -- do not guess these.
// ---------------------------------------------------------------------------
aperture      = 16.7;   // square bore through the eye's front wall, calipers.
                        // Nominal 22.0 mm in CAD at 100%, 16.50 mm at 75%.
aperture_fit  = 0.30;   // clearance so the snout slides rather than forces
snout_len     = 4.5;    // The 16.7 mm bore is only 4.76 mm long: in front of
                        // it sits the stock 18.40 mm pupil recess, 3.26 mm
                        // deep. 4.5 keeps the snout in the bore it locates on.

// ---------------------------------------------------------------------------
// Board. The PCB is NOT flat on either face, and that drove this redesign.
// ---------------------------------------------------------------------------
board_l       = 25.0;   // MEASURED, lug tip to lug tip
board_w       = 11.0;   // MEASURED body width
board_t       = 1.0;    // MEASURED PCB thickness
board_fit     = 0.40;   // clearance around the board in the tray

// FRONT face, the side that looks at the child. Two things stand proud:
// the sensor package at about 1 mm, and the header's solder tails at about
// 2 mm. The board therefore cannot lie flat on the flange. It stands off by
// front_proj and rests on a ledge, with the middle relieved.
front_proj    = 2.5;    // CONFIRM from the photo. Must clear the TALLER of
                        // the two, with a little to spare.
die_h         = 1.0;    // MEASURED package height above the PCB front face
die_pkg_x     = 5.0;    // MEASURED package footprint
die_pkg_y     = 2.7;

// Die position, relative to the CENTRE of the board. The bore is aimed at the
// die, not at the board, so these two numbers decide where the tray sits.
// MEASURED: the package is mid-length, and sits 2.0 mm clear of the long edge
// opposite the header. Package is 2.7 mm across, so its centre is
// 2.0 + 1.35 = 3.35 mm from that edge, i.e. 5.5 - 3.35 = 2.15 mm off the
// board's centreline.
die_x         = 0.0;    // along the board length: mid-length
die_y         = -2.15;  // across the width, towards the edge away from header

header_side   = 1;      // +1 or -1: which long edge carries the header. That
                        // edge gets no ledge and no back support, so a
                        // straight or a right-angle header both clear. The
                        // snout is square, so rotate the part at assembly.

// Optional screws. Listings give M3 at 19.3 mm pitch on diagonal lugs, and no
// two sources agree on anything else, so this stays off until measured.
//
// The holes alone were a defect: at local (+-9.65, +-2.5) they land inside the
// relief cavity, which spans +-11.5 by +-4.5. The screw would have bitten on
// air. post_bosses() now puts material there first.
posts         = false;
lug_pitch     = 19.3;
lug_offset    = 2.5;
post_pilot    = 2.5;    // M3 self-tapping into PLA. 2.9 threads nothing.
post_boss_d   = 5.0;
post_depth    = 2.0;    // blind, into the flange. Total engagement 4.5 mm.

// ---------------------------------------------------------------------------
// Carrier
// ---------------------------------------------------------------------------
flange_w      = 21.0;   // must exceed the aperture so it cannot pull through
flange_t      = 2.5;
bore_back     = 8.0;    // square bore at the flange. Widened from 7.0 because
                        // the board now sits 2.5 mm further back. Not wider
                        // than 8.0: the die sits only 3.35 mm from the board
                        // edge, and the bore eats the pocket wall behind it.
bore_front    = 14.0;   // square bore at the snout mouth
wall_t        = 1.8;
ledge_w       = 1.2;    // how far the ledge reaches in under the board
lead_in       = 0.6;    // tapered length at the snout tip
bore_grooves  = true;   // light traps. Crosstalk is the documented failure
                        // that latches the gate open, so they stay on.
groove_n      = 3;
groove_depth  = 0.6;
groove_h      = 0.7;

// ---------------------------------------------------------------------------
// Back lid. The board is captured on five sides. The sixth, straight out of
// the back, was open. This closes it.
//
// It is a PRESS FIT, not a snap. The carrier is only 3.5 mm deep, so a latching
// finger would be 3.5 mm long and would need about 0.5 mm of deflection. That
// is roughly 7 % surface strain in PLA, far past yield: the finger breaks off
// instead of snapping over. A friction cap has no beam, so it has no strain
// limit. Four crush ribs carry the grip, because a flat interference fit of
// 0.15 mm is inside FDM dimensional noise and a rib is not.
//
// The skirts grip the two SHORT ends only. The part keeps its 15.0 mm width.
// ---------------------------------------------------------------------------
lid_t         = 3.0;    // plate thickness. Was 1.5. The relief below needs
                        // 1.8 mm of it, and 1.2 mm must remain as plate.
lid_relief    = 1.8;    // MEASURED by the owner: components stand about 1 mm
                        // proud of the PCB BACK face. The lid used to sit flat
                        // on that face, so it would have crushed them. The
                        // plate is now hollowed out over the board.
lid_rim       = 1.2;    // the bearing rim, measured in from the pocket edge.
                        // The pocket is 0.2 mm larger than the board per side,
                        // so the rim presses 1.0 mm of bare board edge.
lid_skirt_t   = 1.2;
lid_skirt_h   = 2.9;    // forward reach past the carrier back face. Stops
                        // 0.6 mm short of the flange back face.
lid_gap       = 0.15;   // slip clearance, skirt inner face to carrier
lid_rib       = 0.30;   // rib stands this far proud of that face, so the
                        // interference is 0.30 - 0.15 = 0.15 mm per side
lid_rib_y     = [-4.0, 4.0];  // local to the tray centre
lid_lead      = 0.8;    // ramped length at the skirt tip
lid_ribs      = true;   // off only for the seat clash test
lid_tab_w     = 7.0;    // pull tab, so the lid comes off without a tool
lid_tab_l     = 3.0;
hdr_slot_l    = 17.0;   // header opening in the plate
hdr_slot_w    = 3.5;

// ---------------------------------------------------------------------------
// Derived
// ---------------------------------------------------------------------------
snout_w   = aperture - aperture_fit;
bore_len  = flange_t + snout_len;
tray_l    = board_l + board_fit;
tray_w    = board_w + board_fit;
h         = header_side >= 0 ? 1 : -1;

// Optical path, measured from the TOP of the sensor package, which is what
// actually emits, not from the PCB face.
optical_len = bore_len + front_proj - die_h;
clear_half  = atan((bore_front - 4.4) / 2 / optical_len);
pkg_margin  = (bore_back - max(die_pkg_x, die_pkg_y)) / 2;
back_half   = atan((bore_back  - 4.4) / 2 / (front_proj - die_h));
beam_at_mouth = 4.4 + 2 * optical_len * tan(12.5);

tray_ox   = tray_l + 2 * wall_t;      // 29.0, carrier outer length
tray_oy   = tray_w + 2 * wall_t;      // 15.0, carrier outer width
lid_back  = -front_proj - board_t;    // the carrier back face
lid_in    = tray_ox / 2 + lid_gap;    // skirt inner face, from the tray centre
lid_ox    = tray_ox + 2 * (lid_gap + lid_skirt_t);
hdr_cy    = h * (board_w / 2 - 2.5);  // header centreline, local to the board

assert(flange_w > aperture,
       "flange must be wider than the aperture or it pulls through");
assert(front_proj > die_h,
       "front_proj must clear the sensor package");
assert(clear_half > 12.5,
       "snout bore clips the beam: widen bore_front or shorten snout_len");
assert(back_half > 12.5,
       "flange bore clips the beam: widen bore_back");
assert(bore_front < snout_w, "bore is wider than the snout wall");
assert(pkg_margin > 0.5,
       "package does not clear the flange bore: widen bore_back");
assert(!posts || lug_pitch / 2 - post_boss_d / 2 > bore_back / 2 + 0.5,
       "screw boss fouls the flange bore");
assert(lid_skirt_h < front_proj + board_t,
       "lid skirt reaches the flange back face");
assert(lid_rib > lid_gap,
       "lid ribs do not reach the carrier, so the cap has no grip");
assert(hdr_slot_w > 2.5,  "lid header slot is narrower than the header body");
assert(hdr_slot_l > 15.0, "lid header slot is shorter than the header body");
assert(lid_t - lid_relief >= 1.0,
       "lid plate is thinner than 1.0 mm over the component relief");
assert(lid_rim < ledge_w + (tray_w - board_w) / 2 + 1.0,
       "lid rim reaches further in than the ledge does on the other face");

// ---------------------------------------------------------------------------
// The real board, protrusions and all. Used by the clash test, never printed.
// ---------------------------------------------------------------------------
module board_model(hdr_len = 15.0, hdr_back = 2.5, eps = 0.02) {
    // Every solid is inset by eps. Two parts that merely TOUCH are a legal
    // fit, and without the inset CGAL reports the shared face as a clash.
    translate([-die_x, -die_y, 0]) {
        // PCB, front face at z = -front_proj
        translate([-board_l / 2 + eps, -board_w / 2 + eps,
                   -front_proj - board_t + eps])
            cube([board_l - 2 * eps, board_w - 2 * eps, board_t - 2 * eps]);
        // sensor package, on the front face, on the bore axis
        translate([die_x - die_pkg_x / 2 + eps, die_y - die_pkg_y / 2 + eps,
                   -front_proj + eps])
            cube([die_pkg_x - 2 * eps, die_pkg_y - 2 * eps, die_h - 2 * eps]);
        // header, standing off the BACK face along one long edge
        translate([-hdr_len / 2 + eps, h * (board_w / 2 - 2.5) - 1.25 + eps,
                   -front_proj - board_t - hdr_back + eps])
            cube([hdr_len - 2 * eps, 2.5 - 2 * eps, hdr_back - 2 * eps]);
    }
}

// ---------------------------------------------------------------------------
// Parts
// ---------------------------------------------------------------------------

module bore() {
    hull() {
        translate([0, 0, -front_proj - 0.01])
            linear_extrude(0.02) square(bore_back, center = true);
        translate([0, 0, bore_len])
            linear_extrude(0.01) square(bore_front, center = true);
    }
    if (bore_grooves)
        for (i = [1 : groove_n]) {
            z  = bore_len * i / (groove_n + 1);
            hw = bore_back / 2 + (bore_front - bore_back) / 2 * z / bore_len;
            translate([0, 0, z - groove_h / 2])
                linear_extrude(groove_h)
                    difference() {
                        square((hw + groove_depth) * 2, center = true);
                        square(hw * 2 - 0.2, center = true);
                    }
        }
}

module front() {
    linear_extrude(flange_t) square(flange_w, center = true);
    translate([0, 0, flange_t])
        linear_extrude(snout_len - lead_in) square(snout_w, center = true);
    translate([0, 0, flange_t + snout_len - lead_in])
        hull() {
            linear_extrude(0.01) square(snout_w, center = true);
            translate([0, 0, lead_in])
                linear_extrude(0.01) square(snout_w - 1.2, center = true);
        }
}

// Open tray. The board DROPS IN FROM THE BACK, it does not slide. Sliding
// needed clearance along the whole travel, and the protrusions make that
// impossible. Dropping in needs clearance only where each feature sits.
module tray() {
    translate([-die_x, -die_y, 0]) difference() {
        // block spanning the flange back face down to behind the PCB
        translate([-tray_l / 2 - wall_t, -tray_w / 2 - wall_t,
                   -front_proj - board_t])
            cube([tray_l + 2 * wall_t, tray_w + 2 * wall_t,
                  front_proj + board_t]);
        // board pocket
        translate([-tray_l / 2, -tray_w / 2, -front_proj - board_t])
            cube([tray_l, tray_w, board_t]);
        // relief above the board, inside the ledge
        translate([-tray_l / 2 + ledge_w, -tray_w / 2 + ledge_w, -front_proj])
            cube([tray_l - 2 * ledge_w, tray_w - 2 * ledge_w, front_proj]);
        // the header edge gets no ledge at all
        translate([-tray_l / 2 + ledge_w,
                   h > 0 ? tray_w / 2 - ledge_w : -tray_w / 2,
                   -front_proj])
            cube([tray_l - 2 * ledge_w, ledge_w, front_proj]);
        // open the header edge right through, so the solder tails clear
        translate([-tray_l / 2, h * (tray_w / 2 - ledge_w) - ledge_w / 2,
                   -front_proj])
            cube([tray_l, ledge_w, front_proj]);
    }
}

// Material for the screws to bite. Without this the pilot holes run through
// the relief cavity and thread nothing. Sits above the board, so it does not
// block the drop-in.
module post_bosses() {
    for (i = [-1, 1])
        translate([i * lug_pitch / 2 - die_x, i * lug_offset - die_y,
                   -front_proj])
            cylinder(h = front_proj, d = post_boss_d, $fn = 36);
}

module post_holes() {
    for (i = [-1, 1])
        translate([i * lug_pitch / 2 - die_x, i * lug_offset - die_y,
                   -front_proj - board_t - 0.1])
            cylinder(h = front_proj + board_t + post_depth + 0.1,
                     d = post_pilot, $fn = 24);
}

// RETENTION. The pocket holds the board on all four sides. The only free
// direction is straight out of the back. lid() closes it. Two M3 screws are an
// alternative once the lug pitch is confirmed. Nothing in the eye pushes the
// board backwards, so this is light duty either way.
module carrier() {
    difference() {
        union() {
            front();
            tray();
            if (posts) post_bosses();
        }
        bore();
        if (posts) post_holes();
    }
}

// ---------------------------------------------------------------------------
// Back lid. Print separately. Pushes on from behind, over the two short ends.
// ---------------------------------------------------------------------------
module lid_rib(sx, ry) {
    zb = lid_back - lid_t;
    zt = lid_back + lid_skirt_h - lid_lead;
    hull() {
        translate([sx * lid_in, ry, zb])
            cylinder(h = zt - zb, r = lid_rib, $fn = 20);
        translate([sx * lid_in, ry, zt + lid_lead])
            cylinder(h = 0.01, r = 0.01, $fn = 8);
    }
}

module lid() {
    translate([-die_x, -die_y, 0]) difference() {
        union() {
            // plate, over the whole carrier back face
            translate([-lid_ox / 2, -tray_oy / 2, lid_back - lid_t])
                cube([lid_ox, tray_oy, lid_t]);
            // pull tab, on the edge away from the header
            translate([-lid_tab_w / 2, -h * (tray_oy / 2 + lid_tab_l),
                       lid_back - lid_t])
                cube([lid_tab_w, lid_tab_l, lid_t]);
            // skirts, on the two short ends only
            for (sx = [-1, 1])
                translate([sx > 0 ? lid_in : -lid_in - lid_skirt_t,
                           -tray_oy / 2, lid_back - lid_t])
                    cube([lid_skirt_t, tray_oy, lid_t + lid_skirt_h]);
            // crush ribs, four of them, running along the push direction
            if (lid_ribs) for (sx = [-1, 1], ry = lid_rib_y) lid_rib(sx, ry);
        }
        // relief over the components on the back of the PCB. The lid bears
        // on a rim of bare board edge, not on the components.
        translate([-(tray_l / 2 - lid_rim), -(tray_w / 2 - lid_rim),
                   lid_back - lid_relief])
            cube([tray_l - 2 * lid_rim, tray_w - 2 * lid_rim,
                  lid_relief + 0.01]);
        // the header body and its pins pass straight through
        translate([-hdr_slot_l / 2, hdr_cy - hdr_slot_w / 2,
                   lid_back - lid_t - 0.5])
            cube([hdr_slot_l, hdr_slot_w, lid_t + 1.0]);
        // clearance for the screws, when they are used
        if (posts) post_holes();
    }
}

part = "carrier";          // "carrier" or "lid". Print one, then the other.
if      (part == "lid")     lid();
else if (part == "carrier") carrier();

echo(str("aperture ", aperture, " -> snout ", snout_w, " square, ",
         snout_len, " mm deep"));
echo(str("board stands off the flange by ", front_proj, " mm"));
echo(str("package ", die_pkg_x, " x ", die_pkg_y, " x ", die_h,
         " mm, clears the ", bore_back, " mm bore by ", pkg_margin, " mm"));
echo(str("optical path, package face to snout mouth ", optical_len, " mm"));
echo(str("clear half-angle: snout ", clear_half, " deg, flange ", back_half,
         " deg (need 12.5)"));
echo(str("beam at snout mouth ", beam_at_mouth, " mm in a ", bore_front,
         " mm bore, clearance ", (bore_front - beam_at_mouth) / 2, " mm"));
echo(str("tray ", tray_l, " x ", tray_w, " mm, header edge ",
         h > 0 ? "+y" : "-y", " fully open"));
echo(str("lid ", lid_ox, " x ", tray_oy, " x ", lid_t + lid_skirt_h,
         " mm, grip interference ", lid_rib - lid_gap, " mm per side"));
echo(str("lid relief ", tray_l - 2 * lid_rim, " x ", tray_w - 2 * lid_rim,
         " x ", lid_relief, " mm deep, plate left ", lid_t - lid_relief, " mm"));
echo(str("lid bears on ", lid_rim - (tray_w - board_w) / 2,
         " mm of bare board edge"));
