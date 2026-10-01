# Owner Receipts — human ground truth (Prompt 1, 2026-09-30)

Built by reading the saved transcripts (`out/*_transcript_*.json`) and forum results (titles + snippets), not with an
LLM. Prompts 2–4 are measured against this. Timestamps are the `start_time_text` of the segment where it's said.
Videos read: 3 per product, different channels. "Recurring" = 2+ independent sources (different channels, or a video +
a forum thread). Forum-only clusters are listed separately (Prompt 4 decides whether forums alone can count).

## Video labels (top 10 of `video_results`, in search order)

G = genuine review of this exact model · C = comparison · O = older/other model · U = unboxing/first look ·
S = short (< 3 min) · N = not a review (news, game list) · D = duplicate channel

### Sony WH-1000XM5 (`sony wh-1000xm5 review`)
| # | Title | Channel | Len | Label |
|---|---|---|---|---|
| 1 | Sony WH-1000XM5 Review: Two Steps Forward, One Step Back! | Marques Brownlee | 10:13 | **G** (transcript read) |
| 2 | Sony WH-1000XM5 Headphones Review - 6 Months Later | 6 Months Later | 7:54 | G |
| 3 | Sony WH-1000XM5 - The New King of Noise Canceling Headphones? | Flossy Carter | 35:36 | G (no "review" in title; unboxing-style channel) |
| 4 | Sony WH-1000XM5 Two-Years Later Review: Still Worth BUYING? | SoundGuys | 7:30 | **G** (transcript read) |
| 5 | Sony WH-1000XM5 (Problems & Best Features after 1 Month Daily Use) | Mike O'Brien | 11:55 | **G** (transcript read) |
| 6 | I've been SPOILED! - Sony WH-1000XM5 | ShortCircuit | 13:16 | U (LTT's unboxing channel; title has no "review") |
| 7 | Sony WH-1000XM6 vs Sony WH-1000XM5 - Should you upgrade? | SoundGuys | 9:40 | **C** |
| 8 | Sony WH-1000XM5 - 3 Years Later Review | SoundGuys | 9:55 | G, **D** (SoundGuys again) |
| 9 | Sony WH-1000XM5 headphones - 3 months later \| Mark Ellis Reviews | Mark Ellis Reviews | 8:43 | G |
| 10 | The Headphones I Keep Coming Back To \| Sony XM5 | Edward Lee Films | 13:00 | G (title says "Sony XM5", no "WH-1000") |

Beyond #10: an **O** example (#17 "Sony WH-1000XM6 Review", MKBHD), an other-product example (#18 "Sony **WF**-1000XM5
Earbuds Review"), a how-to (#14 "User Guide"). Shorts sit in `shorts_results`, not `video_results`.

### Nintendo Switch 2 (`nintendo switch 2 review`)
| # | Title | Channel | Len | Label |
|---|---|---|---|---|
| 1 | Nintendo Switch 2 Review - a PC Gamer's Perspective | Linus Tech Tips | 16:12 | **G** (transcript read; dbrand sponsor) |
| 2 | My Nintendo Switch 2 Review (Doesn't Matter?) | Marques Brownlee | 13:17 | **G** (transcript read; dbrand sponsor) |
| 3 | Nintendo Switch 2 Review After 6 Months: Is It Actually Worth It? | Tech Fowler | 19:26 | **G** (transcript read; unrelated sponsor) |
| 4 | The Switch 2 is the Ultimate Value Console | Justin's Game Corner | 11:32 | borderline (opinion piece, no "review") |
| 5 | Nintendo Switch 2 Review - Best Worst Console Ever. | Mrwhosetheboss | 12:13 | G |
| 6 | Nintendo Switch 2 Review | IGN | 20:25 | G |
| 7 | Nintendo Switch 2 – 6 Months Later: Still Worth It in 2026? | Tech Lift | 10:48 | G |
| 8 | Nintendo Switch 2 Review: Everything you NEED to know | SpawnPoiint | 18:27 | G (description: "Unboxing and full review") |
| 9 | Nintendo's Switch 2 Plans Leak Early? & Sony Backs Out of A Big Event \| News Wave | Spawn Wave | 22:55 | **N** (news) |
| 10 | 10 NEW Nintendo Switch 2 eShop Games Worth Buying! | BeatEmUps | 25:16 | **N** (game list) |

Beyond #10: game reviews that name the console ("EA Sports FC 27 Nintendo Switch 2 Review", "Minecraft Dungeons 2 on
Nintendo Switch 2") — the "videos about games" trap: they contain "Nintendo Switch 2" **and** "Review".

### Honeywell 360 heater (`honeywell 360 heater review`) — demo listing is the HHF360V (analog knobs)
| # | Title | Channel | Len | Label |
|---|---|---|---|---|
| 1 | Honeywell 360 Surround Heat Heater HZ-0360 REVIEW | Mr Gizmo | 6:00 | **O** (HZ-0360, a different model code) |
| 2 | Honeywell 360 Space Heater Review | Shopping with Sheryl | 3:24 | G by title, **wrong model in the transcript** (timer + digital up/down thermostat = the digital HHF370, not HHF360V) |
| 3 | Honeywell HHF360VWMC1 360 Surround Heater Slate Review… | THE Reviewer | 1:13 | **S** |
| 4 | 3 WINTERS with the Honeywell 360 and Here's What Happened | UDKodr Reviews | 2:07 | **S** |
| 5 | Honeywell 360 Degree Surround Heater with Fan Forced Technology | The Internet Sorcerer | 4:22 | G? (no "review" in title) |
| 6 | HONEYWELL 360 Surround Heater - Review ✅ | Merry Cottage | 7:03 | **G** (transcript read; analog, "$30 from Walmart") |
| 7 | Honeywell 360° Surround Heater w/ Adjustable Thermostat & Overheat Protection Review by Skywind007 | Skywind007 | 9:49 | **G** (transcript read; 1500 W, 2 settings, knob thermostat) |
| 8 | Honeywell 360° Digital Surround Heater HHF370 - Safety Features | Honeywell Plugged In | 1:39 | **O** + S + maker's own channel |
| 9 | This Tiny Space Heater Actually Works | Mike's World | 6:32 | other product (a 250 W Honeywell, per description) |
| 10 | Honeywell 360 Degree Heater Provides Superb Heat! | Will Reviews | 1:52 | S |

## Sony WH-1000XM5

### Recurring issues
| Issue | Sources |
|---|---|
| **Doesn't fold; big case, worse for travel** | MKBHD 2:15 ("does not fold at all"), 2:30 (case "quite a bit bigger"); Mike O'Brien 9:31 (case large, "doesn't let these fold down") |
| **Transparency mode sounds muffled (vs AirPods Max)** | MKBHD 4:56 ("somewhat muffled version of reality"); Mike O'Brien 1:09 ("more muted… more robotic") |
| **No audio over USB-C** | Mike O'Brien 4:33; SoundGuys 5:13 ("lacks USBC audio") |
| **Gets warm / hot on the ears** | Mike O'Brien 1:59 ("your ears might get a little bit warm"); forum: Reddit r/SonyHeadphones "Review After 2 Years" (snippet: "These get hot") |

### Forum-only cluster (no video raises it)
| Issue | Threads |
|---|---|
| **Headband / hinge breaks** | Sony Community "XM5 Headphone Design Flaw" (Mar 30, 2026; headband breaks at the swivel); Sony Latvia "WH-1000XM5 Broke and Not Covered by Warranty" (left hinge broke); Sony "Sony SH1000XM5 Hinge and Warranty" (Nov 26, 2025; "multiple reports of the same"); JustAnswer "Won't Turn On?" (snippet: "loose ear cup hinges") |
| Random shut-offs / won't power on | Sony Community "keeps shutting down randomly after a pop" (Sep 2, 2025); JustAnswer "Won't Turn On?" (Mar 17, 2026) |

### Recurring praise
| Praise | Sources |
|---|---|
| **Very comfortable, lightweight** | MKBHD 1:12 ("most comfortable… I've ever tried"); Mike O'Brien 1:59, 2:16; SoundGuys 0:54 |
| **Excellent noise cancelling (better on voices/higher frequencies)** | MKBHD 4:09; Mike O'Brien 9:55, 10:18; SoundGuys 1:58 ("about 84%") |
| **Excellent sound quality** | MKBHD 5:11; Mike O'Brien 3:36; SoundGuys 2:40 |
| **~30 h battery, lasts in practice** | MKBHD 6:17; Mike O'Brien 9:48 ("25 30 hours") |
| **Good microphone for calls** | MKBHD 9:10; Mike O'Brien 3:12 |
| App & customization | MKBHD 7:53; SoundGuys 1:36 — **contested**: Mike O'Brien 5:29 calls the app "a little outdated" |

Single-source (not ground truth): bass fatigue (SoundGuys 3:04), older codecs / Bluetooth 5.2 (SoundGuys 4:25), can't use LDAC with two devices (Mike 1:43), no water resistance (Mike 8:03), don't fold flat around the neck (MKBHD 7:11).
Contradiction to watch: AVS Forum snippet reports Bluetooth dropouts; SoundGuys 6:02 says "no stutters no dropouts".

## Nintendo Switch 2

**Sponsor trap:** LTT and MKBHD both carry a dbrand segment. "The screen … is not tough" (LTT 1:00) and "non-removable plastic film … gets scratched super easily" (MKBHD 12:42) are said while selling screen protectors. They agree, but both sit inside ads: **not** counted below.

### Recurring issues
| Issue | Sources |
|---|---|
| **Short battery life in demanding games** | LTT 10:40 (falls well short of Switch OLED); MKBHD 7:00 ("the only real major weak point"), 7:45 (100 to 0 in about 3 h); Tech Fowler 14:47 (2 h 20–2 h 30 on AAA; he calls it "decent" overall); forum: GameFAQs "Any known early defects…" (battery not holding a charge). Amazon insight: battery life mixed |
| **Joy-Con sticks: drift risk (not Hall effect)** | LTT 1:24 ("still susceptible to drifting"), 4:57 (sticks "kind of suck"); MKBHD 10:23 (not Hall effect, "only time will tell"); Tech Fowler 6:15 (some already report drift); forum: Quora "…besides stick drift?" |
| **$80 first-party games** | LTT 6:44; MKBHD 9:23; Tech Fowler 17:27 |
| **Game-key cards: cartridge without the full game** | LTT 7:29; MKBHD 9:41 |
| **Accessories and microSD Express are expensive** | MKBHD 9:15; Tech Fowler 6:44 (microSD), 9:54 (dock ~$125); LTT 12:42 ("ludicrously priced… accessories") |
| **LCD, not OLED** | LTT 8:35 ("bad news … LCD"), 9:23 (min brightness vs OLED); Tech Fowler 8:20 ("unfortunately, no, is not OLED"); forums: Reddit r/NintendoSwitch "common issues" (screen refresh), Famiboards DF Hardware Review (contrast), Digital Foundry "Is the Switch 2 screen really that bad?" (ghosting, Feb 6, 2026). Note: LTT 8:53 says "almost no distracting motion blur", so "ghosting" itself is forum-only |

### Recurring praise
| Praise | Sources |
|---|---|
| **Bigger, better screen (7.9", 120 Hz, HDR)** | LTT 0:16, 8:53; MKBHD 3:03; Tech Fowler 8:48. Amazon insight: Screen size, Graphics |
| **Magnetic Joy-Cons, bigger buttons/sticks** | LTT 0:30; MKBHD 4:05; Tech Fowler 5:36 |
| **Big performance jump; Switch 1 games run better** | LTT 1:39, 2:03; MKBHD 5:45, 6:19; Tech Fowler 11:11. Amazon insight: Responsiveness |
| **Faster loading / snappier UI** | LTT 4:17; MKBHD 6:36. Amazon summary: "faster load times" |
| **Better ergonomics than the original** | LTT 0:37 ("still far from ideal, but worlds ahead"); MKBHD 4:57 ("dramatically more comfortable"); Tech Fowler 2:11, 4:50 |
| **Full-width kickstand** | MKBHD 3:12; Tech Fowler 5:06 |
| **Dock upgrades (4K, fan, Ethernet)** | MKBHD 3:26, 3:31, 3:41; Tech Fowler 9:09, 9:25, 9:33 |
| **Backwards compatible with Switch 1 games** | MKBHD 6:45; Tech Fowler 5:13, 11:22 |

Single-source: VRR not supported when docked (LTT 5:44), mouse mode "fine" (Tech Fowler 6:56; MKBHD 5:13 says responsive but little use; weak), Pro Controller good (LTT 5:29), left Joy-Con release button loose (Nintendo Life thread), dock freezes / black screen (ResetEra thread).

## Honeywell 360 heater (HHF360V)

Thin: 2 genuine analog-model reviews + 1 wrong-model (digital) review, **0 forum results** for both queries.
Treat Sheryl (digital HHF370) as contamination: her timer and digital thermostat praise don't apply to the HHF360V.

### Recurring issues
| Issue | Sources |
|---|---|
| **Noticeable fan noise (average to a little loud)** | Merry Cottage 1:27 ("you definitely know it's on"); Skywind007 8:50 ("average to a little bit loud"), 9:05 (recommends a quieter heater if you need silence). Amazon insight: noise level mixed |

### Recurring praise
| Praise | Sources |
|---|---|
| **Tip-over shut-off** | Merry Cottage 1:42; Skywind007 1:48, 3:17 (demonstrated) |
| **360° heat all around** | Merry Cottage 0:14; Skywind007 0:14 |
| **Light, has a carry handle, easy to move** | Merry Cottage 0:30; Skywind007 7:36, 8:07 |
| **Cheap (~$30–35)** | Merry Cottage 0:45; Skywind007 5:33 |
| Compact | Merry Cottage 1:01. Amazon insight: Size |

Single-source: no exact temperature readout (Merry Cottage 2:19; Amazon: thermostat control mixed), melted plug (Merry Cottage 5:42, blamed on a cheap extension cord; Amazon insight "plug melts": related but not the same claim), overheat sensor (Skywind007 1:56).
