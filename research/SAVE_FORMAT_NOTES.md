# Hobo: Tough Life Save Format Notes

These notes describe findings from the character save files under:

```text
HoboRPG_Data/Save/<Steam account>/NFS_Characters/
```

The active character file for a save is:

```text
<save UUID>_ls
```

All multibyte numbers described below are little-endian.

## Confidence

- The binary layouts and parameter names below are strongly validated.
- Values were compared across six character saves and multiple generations of
  the target save.
- Names were corroborated using the game's IL2CPP metadata in
  `HoboRPG_Data/il2cpp_data/Metadata/global-metadata.dat`.
- Cash editing has been tested successfully in-game.
- Primary current values and their user-facing meanings have been verified
  in-game.
- Secondary values remain unverified and do not currently match the values
  shown in-game.
- Inventory item IDs, quantities, enclosing inventory framing, and observed
  record lengths are validated across 18 save generations.
- The 788-entry NPC flag count, ordered key schema, and boolean value domain
  are identical across the same 18 generations. Flag writes remain
  unverified in-game.
- A controlled slot1 save on June 13, 2026 validated an existing consumable
  stack quantity change in place. Structural inventory writes remain
  unverified.

## Character Parameter Block

The character's primary and secondary parameters are stored near the beginning
of every observed character file.

### Primary parameters

At offset `0xBC`:

```c
int32 entry_count; // 14 in every observed save
```

At offset `0xC0`, `entry_count` records follow:

```c
struct PrimaryParameter {
    int32 parameter_type;
    int32 current_value;
    int32 maximum_value;
}; // 12 bytes
```

The records currently appear in a stable order, but an editor should identify
them by `parameter_type` instead of assuming a fixed position.

| Type | Name | Target entry | Current offset | Maximum offset | Target value |
|---:|---|---:|---:|---:|---:|
| 0 | Health | `0xC0` | `0xC4` | `0xC8` | 86 / 100 |
| 1 | Food | `0xCC` | `0xD0` | `0xD4` | 78 / 100 |
| 2 | Morale | `0xD8` | `0xDC` | `0xE0` | 90 / 100 |
| 3 | Energy (`Freshness`) | `0xE4` | `0xE8` | `0xEC` | 86 / 100 |
| 4 | Warm | `0xF0` | `0xF4` | `0xF8` | 19 / 100 |
| 5 | Wet | `0xFC` | `0x100` | `0x104` | 85 / 100 |
| 6 | Illness | `0x108` | `0x10C` | `0x110` | 3 / 100 |
| 7 | Toxicity | `0x114` | `0x118` | `0x11C` | 0 / 100 |
| 8 | Alcohol | `0x120` | `0x124` | `0x128` | 0 / 100 |
| 9 | Bathroom Need (`Greatneed`) | `0x12C` | `0x130` | `0x134` | 0 / 100 |
| 10 | Smell | `0x138` | `0x13C` | `0x140` | 0 / 100 |
| 20 | Stamina | `0x144` | `0x148` | `0x14C` | 86 / 86 |
| 21 | GearSmell | `0x150` | `0x154` | `0x158` | 30 / 400 |
| 22 | Willpower (`Grit`) | `0x15C` | `0x160` | `0x164` | 5 / 5 |

Names in parentheses are the internal metadata names. The editor uses the
verified user-facing names.

The values are signed 32-bit integers, not floats.

### Secondary parameters

Immediately after the primary records, currently at offset `0x168`:

```c
int32 entry_count;
```

The target save currently has eight secondary records beginning at `0x16C`:

```c
struct SecondaryParameter {
    int32 parameter_type;
    float current_value;
}; // 8 bytes
```

Unlike the primary list, secondary records can be omitted. Their order is not
sorted by parameter ID. Always scan the bounded list for the requested ID.
The mapped secondary values do not currently correspond to the values shown
in-game and should remain read-only.

| Type | Name | Target entry | Value offset | Target value |
|---:|---|---:|---:|---:|
| 16 | Immunity | `0x16C` | `0x170` | 21.0 |
| 18 | Defense | `0x174` | `0x178` | 12.0 |
| 14 | SmellResistance | `0x17C` | `0x180` | 6.0 |
| 13 | ToxicityResistance | `0x184` | `0x188` | 8.0 |
| 15 | GritMax | `0x18C` | `0x190` | 14.0 |
| 17 | Attack | `0x194` | `0x198` | 16.0 |
| 12 | WarmResistance | `0x19C` | `0x1A0` | 5.0 |
| 23 | TemperatureInCelsius | `0x1A4` | `0x1A8` | 3.0 |

Known secondary parameter IDs:

| Type | Name |
|---:|---|
| 11 | WetResistance |
| 12 | WarmResistance |
| 13 | ToxicityResistance |
| 14 | SmellResistance |
| 15 | GritMax |
| 16 | Immunity |
| 17 | Attack |
| 18 | Defense |
| 19 | Charism |
| 23 | TemperatureInCelsius |

IDs 11 and 19 are not present in the current target save. They may be omitted
when their value is zero or otherwise inactive.

## Evidence From Save Generations

Comparing the target `_lws` file with the newer `_ls` file produced changes
consistent with normal gameplay:

| Parameter | `_lws` | `_ls` |
|---|---:|---:|
| Health | 86 | 86 |
| Food | 82 | 78 |
| Morale | 95 | 90 |
| Energy (`Freshness`) | 90 | 86 |
| Warm | 47 | 19 |
| Wet | 85 | 85 |
| Illness | 3 | 3 |
| Alcohol | 4 | 0 |
| Stamina | 90 / 90 | 86 / 86 |
| GearSmell | 0 | 30 |

Another save contains:

```text
Health:    34 / 100
Toxicity:   8 / 100
Alcohol:   60 / 100
```

This provides additional confirmation for parameter IDs 0, 7, and 8.

## Inventory Research

Inventory research is read-only. No inventory values were changed while
collecting these observations.

### Item database

The installed game contains an item database TextAsset at:

```text
Assets/HoboThor/ItemManager/itemDatabaseJson.json
```

It is stored in the UnityFS bundle:

```text
HoboRPG_Data/StreamingAssets/Resources/ResourcesBundle
```

The bundle identifies itself as Unity `2020.3.35f1` and uses LZ4HC block
compression. Its manifest also names the item database path. The project uses
UnityPy `1.25.x` to read the bundle and exact TextAsset paths; implementing only
UnityFS and LZMA support would not read this installation.

The extracted JSON reports `MaxItemID` 681 and contains 680 unique item
definitions:

| Database category | Definitions |
|---|---:|
| `bags` | 9 |
| `companions` | 6 |
| `consumables` | 143 |
| `gears` | 134 |
| `hideoutInteriors` | 166 |
| `scraps` | 184 |
| `sleepingBags` | 6 |
| `weapons` | 32 |

The English localization is a parallel `keys` and `values` mapping with 1,297
unique entries. Twenty-nine catalog records use title key zero and therefore
receive numeric fallback labels. This is valid observed data, not a catalog
error.

The database provides category-specific gameplay properties but no verified
save-record framing, stack limit, reference identity, or complete serialized
default instance. Catalog extraction alone must not enable inventory writes.

The item database and its English `strings_items` localization resolve the
three known slot5 items as follows:

| Item ID | Name | Database category | Title key |
|---:|---|---|---:|
| 349 | Deodorant | `consumables` | 649 |
| 203 | Bandage | `consumables` | 383 |
| 202 | Antidepressants | `consumables` | 381 |

Quest data independently refers to Deodorant as `_item_349_` and Bandage as
`item_203`, providing a second source for those two mappings.

### Inventory framing and records

Every observed item begins with this 15-byte base form:

```c
struct BaseItemRecord {
    byte present;          // always 1 in the observed populated records
    int32 item_id;
    int32 serialize_type;
    int32 quantity;
    byte flag_a;
    byte flag_b;
};
```

The field names `BaseItemSaveData`, `quantity`, `hasMinigame`, and
`TypeForSerialize` occur in the game's IL2CPP metadata. This corroborates the
quantity and serialization-type interpretation. One trailing byte may be
`hasMinigame`, but the order and meaning of both trailing flags are not yet
established. The leading byte is likely an object-presence marker.

The observed serialization types and complete record lengths are:

| Type | Catalog category | Record bytes | Additional payload |
|---:|---|---:|---:|
| 1 | `gears` | 31 | 16 |
| 2 | `bags` | 15 | 0 |
| 3 | `consumables` | 15 | 0 |
| 4 | `hideoutInteriors` | 19 | 4 |
| 5 | `scraps` | 15 | 0 |
| 7 | `sleepingBags` | 19 | 4 |
| 8 | `weapons` | 19 | 4 |
| 9 | `companions` | 15 | 0 |

Type 9 has been observed in equipped-item data but not in carried inventory.
The meanings of all additional payload fields remain unverified, so the parser
retains those bytes without interpretation.

The active inventory is structurally preceded by a nullable saved-bag slot
collection:

```c
int32 saved_bag_slot_count;
Nullable<BaseItemRecord> saved_bags[saved_bag_slot_count]; // type 2 or 0 byte
int32 inventory_count;
BaseItemRecord inventory[inventory_count];  // type-specific lengths
```

The read-only parser requires this complete framing, validates every item ID
against the catalog, validates serialization type/category agreement, and
rejects missing, truncated, unknown, or ambiguous regions. It resolves all 18
available `_ls`, `_lws`, `_b1`, and `_b2` character generations, with observed
inventory counts from 3 through 140.

For the current slot5 `_ls`:

```text
saved-bag count offset: 0x286
saved-bag count:        3
inventory count offset: 0x2B7
inventory count:        140
inventory records:      [0x2BB, 0xB9F)
```

The inventory contains seven gear records, four bag records, 55 consumable
records, 13 hideout-interior records, 58 scrap records, and three sleeping-bag
records. The consumable run begins at `0x3D0` and ends at `0x709`.

The three previously tracked consumables in the current capture are:

| Record offset | Item | ID | Type | Quantity offset | Quantity | Flags |
|---:|---|---:|---:|---:|---:|---|
| `0x5A1` | Deodorant | 349 | 3 | `0x5AA` | 6 | `01 00` |
| `0x5B0` | Bandage | 203 | 3 | `0x5B9` | 12 | `01 00` |
| `0x5BF` | Antidepressants | 202 | 3 | `0x5C8` | 8 | `01 00` |

IL2CPP metadata also contains `GetInventorySaveData`,
`WriteBaseItemsSaveData`, `ReadInventorySaveData`, `InventoryTypeOrder`, and
inventory category-sorting methods. This supports the interpretation of the
second integer as a serialization/category discriminator.

### Evidence from existing generations

Inventory count offsets move between characters and generations. Observed
offsets range from `0x1FF` through `0x2B7`; record regions range from three
items through 140. Other collections contain the same item-record shapes, so
searching for an item ID, count, or 15-byte pattern remains insufficient.
The saved-bags/inventory framing is required to distinguish the active
inventory from death items, buybacks, and other item collections.

The read-only generation comparison command confirms why broad byte-diff
searches are insufficient. For the current slot1 capture, `_lws` and `_ls` are
both 65,536 bytes but differ at 31,575 offsets across 3,572 separate spans.
Controlled saves with one deliberate inventory action are required to isolate
inventory changes from normal character, quest, and runtime state changes.

### Controlled quantity capture

On June 13, 2026, slot1 was captured before and after consuming exactly one
Scrap food in-game. The game generated both saves; the editor did not modify
either capture.

| Property | Before | After |
|---|---:|---:|
| Inventory count | 70 | 70 |
| Inventory records | `[0x295, 0x6B7)` | `[0x295, 0x6B7)` |
| Scrap food ID 307 record | `0x2C6` | `0x2C6` |
| Quantity | 8 | 7 |

The baseline SHA-256 was
`5258b6f7997d99dac550f0f72e3f8f9d9a80947375c5c748ee3edaf68c37fb05`;
the post-action SHA-256 was
`614ad935fd19c1b1908fbd7df68a67acf5a1820c699736d225088e0b987dcd8d`.

The files differed at 35 byte offsets across 26 spans due to normal gameplay
and save-state updates. Inside the active inventory region, only offset
`0x2CF` changed, from `08` to `07`. This is record offset `0x2C6` plus nine
bytes, exactly the little-endian `int32 quantity` field in the base record.

This validates in-place quantity serialization for a retained consumable
stack. It does not validate increasing beyond game stack limits, retaining a
zero quantity, deleting a depleted stack, adding a record, resizing the
inventory region, or changing equipment references.

### Controlled editor-generated decrement

After the game-generated capture above, the research-gated writer staged
Scrap food ID 307 from quantity 7 to 6 in live slot1. Before writing, it
required the exact source SHA-256
`614ad935fd19c1b1908fbd7df68a67acf5a1820c699736d225088e0b987dcd8d`.
It created and verified a complete external backup, reparsed the generated
bytes, and atomically replaced the active file.

The resulting SHA-256 is
`732b775f5f5b5277ae4c73733370ad70329619bd5dfc923170be62242a1ac005`.
An independent comparison found exactly one changed byte at `0x2CF`, from
`07` to `06`. The inventory count remains 70 and its record region remains
`[0x295, 0x6B7)`.

The game successfully loaded this writer-generated save and displayed Scrap
food quantity 6. After a normal save and exit, the active file SHA-256 was
`3f0acfc57cc7784352f34d20c18fec59cce2e8e654aecf52fc7d25b105101272`.
Compared with the exact editor output, the game changed 20 bytes across 14
spans, all outside the inventory region. Inventory framing, all 70 records,
and every inventory byte were preserved.

This validates that the game accepts a positive in-place decrement of an
existing consumable stack produced by the research-gated writer.

### Controlled quantity increase

Slot1 was next captured before and after acquiring exactly one Scrap food
in-game. The source SHA-256 was
`3f0acfc57cc7784352f34d20c18fec59cce2e8e654aecf52fc7d25b105101272`;
the post-action SHA-256 was
`6108599486799fcf766a024dfc4f8ed0864bb9311fdfc81c6e60e6315c802758`.

Normal gameplay changed 10,120 file offsets across 679 spans. Inside the
inventory region, only `0x2CF` changed, from `06` to `07`. Inventory count,
boundaries, ordering, item identity, flags, and payloads were unchanged.
Together with the earlier game-generated quantity 8 capture, this validates
the observed Scrap food range through quantity 8.

The research gate therefore permits Scrap food ID 307 increases only by one
item per staged change and only up to quantity 8. The catalog contains no
trustworthy stack-limit property, so generic consumable increases remain
blocked.

The writer then staged the validated live increase from quantity 7 to 8. It
required source SHA-256
`6108599486799fcf766a024dfc4f8ed0864bb9311fdfc81c6e60e6315c802758`,
created a verified external backup, and produced SHA-256
`8193f98bd0f591df74889092c5e04c37a85f81ee81be46a8ab5845b2ca395767`.
An independent comparison found exactly one changed byte at `0x2CF`, from
`07` to `08`.

The game successfully loaded this writer-generated save and displayed Scrap
food quantity 8. After a normal save and exit, the active file SHA-256 was
`4f90b86b3fb59fbf36fa6bd136c0038e49d600d17455c87854773218d3f051be`.
Compared with the exact editor output, the game changed 18 bytes across 10
spans, all outside inventory. Inventory framing, all 70 records, and every
inventory byte were preserved. This validates the research-gated Scrap food
increase through quantity 8 in-game.

### Controlled quantity-one depletion

Consuming the only Roll (ID 1) changed the inventory count from 70 to 69 and
shortened the inventory region from `[0x295, 0x6B7)` to
`[0x295, 0x6A8)`. The removed record was the 15 bytes at `0x302`:

```text
01 01 00 00 00 03 00 00 00 01 00 00 00 00 00
```

The complete post-action inventory stream exactly equals the prior stream
with that record removed. Every following item and the entire serialized
suffix shifted left by 15 bytes without alteration, and 15 zero-padding bytes
were appended to retain the 65,536-byte file size. A reconstruction using
only those operations differs from the game-generated save at 19 unrelated
runtime bytes.

The research gate now implements this exact structural deletion, restricted
to the validated quantity-one Roll. Other removals remain blocked pending
their own reference and payload evidence.

Applying that deletion primitive to a copy of the real pre-depletion save
produced the same count, inventory end, item sequence, shifted suffix, and
padding as the game-generated save. The output differed only at the same 19
unrelated runtime bytes, and its external backup matched the source exactly.

### Controlled Roll insertion

Acquiring exactly one Roll while it was absent restored the count from 69 to
70 and the inventory region from `[0x295, 0x6A8)` to
`[0x295, 0x6B7)`. The game inserted this exact default record at `0x302`:

```text
01 01 00 00 00 03 00 00 00 01 00 00 00 00 00
```

The record is identical to the previously removed Roll. Removing it from the
new stream reproduces the complete prior 69-item stream exactly. Roll catalog
index `20008` places it between Strained alcohol index `20003` and Chewing gum
index `20013`, matching the observed inventory ordering. The game shifted the
serialized suffix right by 15 bytes and consumed 15 trailing zero-padding
bytes while retaining the 65,536-byte file size.

The resulting inventory stream is byte-identical to the earlier known-good
70-item inventory. The research gate implements this exact default insertion,
restricted to one absent Roll at its validated catalog-order position.

Applying the insertion primitive to the real absent-Roll capture produced an
inventory stream byte-identical to the game-generated insertion and a
byte-identical external backup. Only 32 unrelated runtime bytes differed
between the writer and game outputs.

For live validation, the writer then removed the Roll from source SHA-256
`97be712aa6ad1919b2e56fe392b64d434d62f169c1ec24d263368d03e2394387`.
The resulting SHA-256 is
`bb9463dc82d9c80d212be4094a68d78b6f87042e2257da4387467dd889079e03`.
An independent reconstruction exactly matches the output: count 69, Roll
absent, region end `0x6A8`, unchanged shifted suffix, and restored padding.
This writer-generated removal is awaiting in-game load validation.

The game loaded that writer-generated removal and showed the Roll absent.
After a normal save and exit, SHA-256 was
`06687af4c4a21b363bccb039e272589665c38e26f1f5f8d52f09a9af8b8b76c5`.
Compared with the exact writer output, 28 runtime bytes changed outside
inventory and every inventory byte was preserved. This validates the
writer-generated structural deletion in-game.

The writer then reinserted the Roll into that accepted source, producing
SHA-256
`2e242ad775dcad50c08648e1045050d94e616bddd856f281ff9c2f428af62db6`.
Independent reconstruction exactly matches the output: count 70, region end
`0x6B7`, and the validated default record at `0x302`. This writer-generated
insertion was accepted by the game and displayed as quantity 1. After a
normal save and exit, SHA-256 was
`22d04fade94a3e9c48c4b5f3c0d6f18fa62fe64dac0e191ee326161dff32a572`.
Compared with the exact writer output, 18 runtime bytes changed outside
inventory and every inventory byte was preserved. This validates the
writer-generated Roll insertion in-game.

### Equipment framing

The current slot1 equipped-item block ends immediately before the saved-bags
count at `0x260`. It contains six nullable slots in this order:

1. Hat, gear catalog category 0
2. Jacket, gear category 1
3. Trousers, gear category 2
4. Shoes, gear category 3
5. Companion, serialization type 9
6. Weapon, serialization type 8

Populated slots use their complete item record; empty slots use one zero byte.
In current slot1, the four gear slots contain Scarf, Friedrich's jacket,
Friedrich's pants, and Friedrich's shoes. Companion is empty at `0x24C`, and
Rolling pin ID 532 occupies the 19-byte weapon slot at `0x24D`. The carried
inventory contains Lame thingamajig ID 521 at `0x295`, also a 19-byte weapon.

Metadata independently names `ReadEquippedItemsSaveData`,
`WriteEquippedItemsSaveData`, `GetEquipedGear`, `SLOT_JACKET`,
`SLOT_TROUSERS`, `SLOT_SHOES`, `SLOT_COMPANION`, and `SLOT_WEAPON`.

### Controlled weapon swap

Equipping carried Lame thingamajig ID 521 in place of Rolling pin ID 532 kept
the inventory count at 70 and all equipment, bag, and inventory boundaries
unchanged. The game exchanged the complete 19-byte records:

```text
Equipped 0x24D:
  before 01 14 02 00 00 08 00 00 00 01 00 00 00 01 00 12 00 00 00
  after  01 09 02 00 00 08 00 00 00 01 00 00 00 01 00 14 00 00 00

Carried 0x295:
  before 01 09 02 00 00 08 00 00 00 01 00 00 00 01 00 14 00 00 00
  after  01 14 02 00 00 08 00 00 00 01 00 00 00 01 00 12 00 00 00
```

Each item retained its complete payload unchanged. Only four byte offsets in
those records differed; normal gameplay changed 18 additional runtime bytes.

The research gate implements this exact record exchange, restricted to the
validated weapon pair IDs 521 and 532 and to equipment layouts that pass the
strict six-slot parser. Applying it to a copied post-swap save restored the
prior equipped and carried records exactly with a byte-identical backup.

For live validation, the writer reversed the swap from source SHA-256
`f4b5b3578bf3eb4f03c717dacd5b30dfedbd2cc2905794dd2527416a8acbb9da`,
producing SHA-256
`0fd5c7fa2559235a13fd6626c96fcc7d502f47f4d163ae002764d2e4e799b2f7`.
Independent reconstruction matches exactly. Only offsets `0x24E`, `0x25C`,
`0x296`, and `0x2A4` changed as the two complete records were exchanged.
This writer-generated reverse swap is awaiting in-game validation.

The game loaded the reverse swap and showed Rolling pin equipped with Lame
thingamajig carried. After a normal save and exit, SHA-256 was
`ef0929e5afbbb4389676fa43b5f2d29d9ffa7a38f88129ac8503460588e2e05d`.
Compared with the exact writer output, 14 runtime bytes changed and zero bytes
changed in the complete equipment-plus-inventory region. This validates the
writer-generated weapon swap in-game.

### Controlled Scarf unequip

Unequipping Scarf ID 167 replaced its 31-byte equipped hat record at `0x1D0`
with one zero byte. The remaining equipment and saved-bags framing shifted
left 30 bytes, moving the saved-bags count from `0x260` to `0x242`.

The game inserted the complete unchanged Scarf record into carried inventory
at `0x28A`, after the carried weapon and before bags. Inventory count changed
70 to 71, and inventory end changed from `0x6B7` to `0x6B8`. The 31-byte
inventory insertion shifted subsequent serialized data right, for a net
one-byte growth after the 30-byte equipment contraction; one trailing
zero-padding byte was consumed to retain the 65,536-byte file size.

Reconstructing exactly those operations reproduces every equipment and
inventory byte in the game-generated save. Only nine unrelated runtime bytes
differ. This validates the hat empty marker, gear record transfer, and
carried-gear category position for Scarf.

### Controlled Scarf re-equip

Re-equipping Scarf performed the exact inverse transformation. Its unchanged
31-byte carried record replaced the one-byte empty hat marker, shifting the
remaining equipment and inventory framing right 30 bytes. The carried record
was deleted, inventory count changed 71 to 70, inventory end changed from
`0x6B8` to `0x6B7`, and one zero-padding byte was appended.

Reconstruction reproduces every equipment and inventory byte in the
game-generated save with SHA-256
`3c2db31b98af4f71459e89d4abab174b13481c5bc28b2d7e2a7f8db6a4a13b4a`.
Only ten unrelated runtime bytes differ. The reconstructed region also
matches the accepted pre-unequip region exactly.

The research gate now implements both directions, restricted to Scarf ID 167
and the hat slot. It requires the observed weapon/gear/bag ordering, an exact
source hash, trailing zero padding, successful reparsing, unchanged other
equipment records, and exact count and boundary changes.

The live writer unequipped Scarf from the hash above, creating external backup
`fa5726ae-cb04-4a29-9229-24ed23e45644_ls.bak-20260613-210857` and producing
SHA-256
`ddc2f727fb6086e8bb4b890cdc092daf078efa35c84cd6569f17bc9298f28d86`.
The generated file has an empty hat slot, Scarf carried at `0x28A`, inventory
count 71, and unchanged 65,536-byte size.

The game loaded the writer-generated unequip and showed the hat slot empty
with Scarf carried. After saving, SHA-256 was
`df84366f1a0425f287e35f42d549a896384b5ca709ac5bbc1a4d63625f77b9ec`.
Compared with the exact writer output, only eight runtime bytes changed, all
before the equipment region. The complete equipment-plus-inventory region was
preserved.

The live writer then re-equipped Scarf from that accepted source, creating
external backup
`fa5726ae-cb04-4a29-9229-24ed23e45644_ls.bak-20260613-212759` and producing
SHA-256
`8e342c5f1d885f632f5cbe64af830f85cdd31d5c78ad136a3ed3b7b47bfb4df4`.
The generated file has Scarf equipped, no carried Scarf record, inventory
count 70, inventory region `[0x295, 0x6B7)`, and unchanged 65,536-byte size.

The game loaded the writer-generated re-equip and showed Scarf equipped and
absent from carried inventory. After saving, SHA-256 was
`c2a19756fd4dbc533ccc65f9175e48f92e6add5bc660a987432e86b05cb175b8`.
Compared with the exact writer output, only seven runtime bytes changed, all
before the equipment region. The complete equipment-plus-inventory region was
preserved.

### Controlled bag swap

After healing the character, swapping equipped Plastic bag ID 289 for carried
Makeshift luggage ID 290 changed the parsed saved-bags and carried-inventory
region. The source `_lws` SHA-256 was
`48ec68c116e67ae2efaa46ace7a7a9320f48cd91b1aaa56c26866d59845930b4`;
the post-swap `_ls` SHA-256 was
`28f1817fa7a42659facf1cc49fc7af3815da38b3b10b3e67e1dec94b59f87068`.

The six parsed equipment slots were unchanged. The third saved-bag record at
`0x282` changed from Plastic bag to Makeshift luggage:

```text
before 01 21 01 00 00 02 00 00 00 01 00 00 00 00 00
after  01 22 01 00 00 02 00 00 00 01 00 00 00 00 00
```

The carried inventory count changed from 70 to 69, and the inventory region
changed from `[0x295, 0x6B7)` to `[0x295, 0x6A8)`. The carried Makeshift
luggage record at `0x2A8` and carried Plastic bag record at `0x2B7` were
replaced by one carried Plastic bag record at `0x2A8` with quantity 2:

```text
removed 01 22 01 00 00 02 00 00 00 01 00 00 00 00 00
removed 01 21 01 00 00 02 00 00 00 01 00 00 00 00 00
added   01 21 01 00 00 02 00 00 00 02 00 00 00 00 00
```

This capture suggests bag equip replacement moves the new bag into the
saved-bags collection and returns the displaced bag to carried inventory,
where it can merge with an existing identical carried bag stack. A writer must
validate the inverse swap and a non-merging replacement before exposing bag
equipment edits.

Swapping back from equipped Makeshift luggage to Plastic bag produced the
inverse transformation. The source `_lws` SHA-256 was
`28f1817fa7a42659facf1cc49fc7af3815da38b3b10b3e67e1dec94b59f87068`;
the post-swap `_ls` SHA-256 was
`985594db21c97fae6e14275840feb50ac845979341508691b8933fc76a8fa06b`.
The third saved-bag record at `0x282` changed from Makeshift luggage back to
Plastic bag:

```text
before 01 22 01 00 00 02 00 00 00 01 00 00 00 00 00
after  01 21 01 00 00 02 00 00 00 01 00 00 00 00 00
```

The carried inventory count changed from 69 to 70, and the inventory region
changed from `[0x295, 0x6A8)` to `[0x295, 0x6B7)`. The carried Plastic bag
quantity-2 record at `0x2A8` split into carried Makeshift luggage at `0x2A8`
and carried Plastic bag at `0x2B7`:

```text
removed 01 21 01 00 00 02 00 00 00 02 00 00 00 00 00
added   01 22 01 00 00 02 00 00 00 01 00 00 00 00 00
added   01 21 01 00 00 02 00 00 00 01 00 00 00 00 00
```

Together these two captures validate bag replacement with merge and split
behavior for Plastic bag and Makeshift luggage. A non-merging replacement is
still needed to confirm displaced-bag insertion when no identical carried bag
stack exists.

Buying one additional Stylish satchel ID 292 inserted a carried bag record
without changing saved bags or the six parsed equipment slots. The source
`_lws` SHA-256 was
`ecfe1b6b478025a38b2af9129f6102d08bfdcb3451711968ffaffd2a00cb9d04`;
the post-purchase `_ls` SHA-256 was
`518c0409d5ac4064e57322c3f0f838aabb8aed18138a17a12aa7f7771110d014`.
The carried inventory count changed from 70 to 71, and the inventory region
changed from `[0x295, 0x6B7)` to `[0x295, 0x6C6)`.

The new record was inserted at `0x2C6`, after the carried bag records and
before consumables:

```text
added 01 24 01 00 00 02 00 00 00 01 00 00 00 00 00
```

Unequipping the equipped Stylish satchel while that carried Stylish satchel
was present changed saved-bag slot 2 from a 15-byte populated record to a
one-byte empty marker. The source `_lws` SHA-256 was
`518c0409d5ac4064e57322c3f0f838aabb8aed18138a17a12aa7f7771110d014`;
the post-unequip `_ls` SHA-256 was
`515d35ea637bca5490a2eefc5e9705f90178939f1ceefa84cbfea043c121f72a`.

The saved-bag slot count remained 3, but populated saved bags changed from 3
to 2. Slot 2 changed from:

```text
before 01 24 01 00 00 02 00 00 00 01 00 00 00 01 00
after  00
```

The carried inventory count remained 71, while the inventory start shifted
from `0x295` to `0x287` because the saved-bag slot shrank by 14 bytes. The
carried Stylish satchel record at `0x2C6` became a quantity-2 record at
`0x2B8`:

```text
removed 01 24 01 00 00 02 00 00 00 01 00 00 00 00 00
added   01 24 01 00 00 02 00 00 00 02 00 00 00 00 00
```

This validates nullable saved-bag slots and bag unequip merging when an
identical carried bag stack already exists. A non-merging bag unequip remains
needed to confirm insertion when no identical carried stack exists.

Unequipping Cloth bag ID 291 while no carried Cloth bag existed validated the
non-merging unequip path. The source `_lws` SHA-256 was
`515d35ea637bca5490a2eefc5e9705f90178939f1ceefa84cbfea043c121f72a`;
the post-unequip `_ls` SHA-256 was
`b827964456c62fcec31c53fe4e641b44bef3dff20a83ef5757b973b0f1076ff1`.

The saved-bag slot count remained 3, and saved-bag slot 1 changed from a
15-byte Cloth bag record to a one-byte empty marker:

```text
before 01 23 01 00 00 02 00 00 00 01 00 00 00 01 00
after  00
```

The carried inventory count changed from 71 to 72, and the inventory region
changed from `[0x287, 0x6B8)` to `[0x279, 0x6B9)`. A new carried Cloth bag
record was inserted at `0x2AA`, after carried Plastic bag and before carried
Stylish satchel:

```text
added 01 23 01 00 00 02 00 00 00 01 00 00 00 01 00
```

Together with the Stylish satchel capture, this validates saved-bag unequip
for both merge and non-merge outcomes.

The follow-up controlled capture equipped the carried Cloth bag back into the
empty saved-bag slot. The source `_lws` SHA-256 was
`b827964456c62fcec31c53fe4e641b44bef3dff20a83ef5757b973b0f1076ff1`;
the post-equip `_ls` SHA-256 was
`9790334536c38d783082492e7ef490bc36923580f93e75322f6b6c81f9ed3f9d`.

Saved-bag slot 1 changed from a one-byte empty marker back to the complete
Cloth bag record:

```text
before 00
after  01 23 01 00 00 02 00 00 00 01 00 00 00 01 00
```

The carried inventory count changed from 72 to 71, and the inventory region
changed from `[0x279, 0x6B9)` to `[0x287, 0x6B8)`. The carried Cloth bag
record at `0x2AA` was deleted. This validates the inverse non-merge saved-bag
transfer for Cloth bag ID 291.

### Current safety conclusion

- The active inventory's saved-bags prefix, outer count, boundaries, item IDs,
  quantities, serialization types, and observed record lengths are strongly
  supported across 25 generations.
- The parser preserves all category-specific payload bytes.
- Existing research-gated APIs still preserve the older narrow validations.
  The full TUI inventory writer now rebuilds the equipment, saved-bags, and
  carried-inventory region from staged raw records, requires the exact source
  SHA-256, reparses the result, and writes through the existing backup and
  atomic replacement path.
- Payload meanings, stack limits, most record defaults, quick-slot/death-item
  /buyback references, and external stash/container references remain
  unverified. Newly added records use conservative zero-filled payload bytes
  unless a moved existing record already provides game-generated payloads.
- Companion framing is not yet represented in any observed carried inventory.
- Normal TUI inventory mutation exposes staged add, remove, quantity, equip,
  unequip, discard, and apply operations. Game-load validation is still needed
  for broad default-record construction across every item category.

## Cash

Cash is stored in a separate dictionary of named signed 32-bit integers.

Locate this byte sequence:

```text
04 63 61 73 68
^  c  a  s  h
|
one-byte string length
```

The four bytes immediately following `cash` are the little-endian `int32`
value:

```text
04 63 61 73 68 XX XX XX XX
               ^^^^^^^^^^^
               cash value
```

Do not hard-code the cash offset. It moves as preceding quest/progression data
changes.

For the current target file:

```text
cash key offset:   0x7422 (decimal 29730)
cash value offset: 0x7427 (decimal 29735)
cash bytes:        10 27 00 00
cash value:        10000
```

The same field was previously observed at a different offset, confirming that
searching for the serialized key is safer than using an absolute location.

Other named integers near cash include:

```text
crime
DealerJuniorNum
DezolatDrunkMeter
DigHoleProgress
DogProgress
FetchKapsarinaNum
FortDobreSkutky
GeneralDrinking
StatusDealerNum
StatusZlodejNum
```

Most of these appear to be quest or progression state rather than general
character attributes.

## World Day And Season

Day and season are stored in the matching world save file:

```text
HoboRPG_Data/Save/<Steam account>/NFS_Worlds/<save UUID>_ls
```

The observed world files begin with this little-endian float/version marker:

```text
AE 47 E1 3E // 0.44
```

The currently supported season field is a fixed signed `int32` header value.
The displayed day is derived from the signed `int32` raw time value:

```text
day = floor(raw_time / 1080) + 1
```

The `1080` divisor matches an 18-hour in-game day. When editing the displayed
day, the editor preserves `raw_time % 1080`, so changing the day does not also
jump the time of day.

The day editor is capped to `1-30`, matching the wiki-confirmed length of one
season. The season editor intentionally has no gameplay cap while this field is
being tested; it only enforces the non-negative signed `int32` storage range.
Day still writes the raw-time field rather than the unrelated value at `0x14`.

| Field | Offset | Supported range | Observed active values |
|---|---:|---:|---|
| Season | `0x08` | `0-2147483647` | `0`, `1` |
| Raw time backing Day | `0x04` | displayed day `1-30` | `5031`, `15480`, `24402` |

Observed slot/world pairs:

| Slot | Save UUID | Raw time | Season | Displayed day |
|---|---|---:|---:|---:|
| `slot5` | `e1f4a340-ecbf-4f71-a80f-8f2ed6b26b3b` | `24402` | `1` | `23` |
| `slot1` | `fa5726ae-cb04-4a29-9229-24ed23e45644` | `15480` | `0` | `15` |
| `slot0` | `076cd3aa-012e-45ba-b41b-5de616db15ae` | `15467` | `0` | `15` |

The value at `0x14` was initially misidentified as Day because it contained
values in a plausible day range in some saves. A live check showed the game
still displayed day 15 after the editor changed `0x14` to `1`, while `0x04`
was `15467`; `floor(15467 / 1080) + 1 == 15`. The editor changes only the
selected four-byte raw-time or season field and writes through the shared
backup and atomic verification path.

## Reputation Table And Editing

Reputation is structurally readable and writable through a staged TUI screen.
Reverse engineering of this Linux IL2CPP build established:

- `Game.ReputationSaveData` contains two `int32` fields in serialization
  order: `archetypeId`, then `value`.
- The `Archetype` enum assigns `Specific_Bruno` the value `28`.
- The character save contains one top-level list of 113 reputation records.
- The same 113 archetype IDs occur in the same order in all six available
  character saves, while the table's absolute offset moves with preceding
  variable-length data.

The reader identifies that complete record sequence and then selects
records by archetype ID. The editor has verified names for all 113 serialized
IDs and does not search for isolated integers equal to displayed values.
Bruno-named strings such as `BrunoSpoluprace` and `BrunoNasrani` are separate
quest/progression state.

## NPC Quest And Progression Flags

All 18 available character generations contain one structurally consistent
boolean dictionary:

```c
int32 entry_count; // 788

struct NpcFlagEntry {
    string key; // 7-bit encoded byte length + printable ASCII bytes
    int32 value; // always 0 or 1 in observed saves
};
```

The 788 keys are unique and occur in the same order in every observed save.
The table's absolute offset moves as preceding variable-length data changes.
The editor therefore locates the table by its count and a SHA-256 fingerprint
of the complete ordered key sequence, then requires every value to be boolean.
It does not search for an individual key or use a fixed offset.

Observed keys include NPC dialogue, quest, and global progression state such
as `BrunoSpoluprace`, `BrunoNasrani`, `DogProgress`, and
`PrvniKralovskyQuest`. Their individual gameplay semantics and dependencies
are not comprehensively known. Writes preserve the table size and keys and
only replace explicitly selected four-byte values. Arbitrary combinations
may create inconsistent quest progression and require controlled in-game
validation.

The installed `ResourcesBundle` also contains raw quest and conversation JSON
that refers to these values as `bool_<key>_0` and `bool_<key>_1`. Parallel
English conversation assets resolve dialogue IDs. The editor uses this data
read-only to associate flags with every directly referencing NPC and quest and
to display a concise English context. When no useful direct context exists, it
splits the Czech-derived CamelCase key and applies a small glossary; those
notes are explicitly marked `(inferred from key)`. Annotation extraction is
informational and is not part of save-format validation.

Quest discovery is built from the same read-only asset data. The installed
bundle currently exposes 294 quest JSON definitions, and those definitions
refer to the boolean table through `bool_<key>_0` and `bool_<key>_1` tokens.
The editor correlates those references with the selected save to show likely
quest status and flag evidence. These status labels are inferred; no separate
authoritative quest-state structure has been validated yet.

Inspect a character file read-only with:

```sh
hobo-save-research bruno-reputation-inspect \
  --character "/path/to/character_ls"
```

For the slot 5 baseline, the structural result is:

```text
reputation count offset: 0x146B
reputation records: [0x146F, 0x17F7)
Bruno record offset: 0x14EF
Bruno value offset: 0x14F3
Bruno value: 80
```

These absolute offsets describe only that capture. The parser locates the
table from its count and complete archetype sequence on every read.

The writer locates and reparses the table structurally, requires the staged
source hash and expected current values, limits new values to 0 through 100,
changes only selected four-byte `value` fields, and uses the standard
verified backup and atomic replacement path.

The TUI can stage changes for multiple records and apply them in one write and
one backup. Search accepts the display name, raw enum name, or archetype ID.

An initial direct-write trial was applied on June 14, 2026:

```text
slot: slot5
Bruno value: 80 -> 81
live value offset for this generation: 0x1499
file size before and after: 65536 bytes
only byte difference: 0x1499, 0x50 -> 0x51
backup: ~/.local/share/hobo-save-editor/backups/
  e1f4a340-ecbf-4f71-a80f-8f2ed6b26b3b_ls.bak-20260614-082709
```

The save reparsed successfully after replacement, and the game displayed the
new Bruno trust value.

## Safe Editing Guidance

1. Close the game before reading or modifying a save.
2. Back up the complete `_ls` file.
3. Parse counts and records instead of blindly writing fixed offsets.
4. Verify the expected parameter ID before changing its value.
5. Preserve file size and every unrelated byte.
6. Keep primary current values within a sensible range, normally zero through
   the stored maximum.
7. Treat secondary values as IEEE-754 little-endian `float32`.
8. Keep maximum values and all secondary values read-only.
9. Reopen the game and verify the result after editing.

## Still Unknown

- Exact semantics and direction of `Wet`, `GearSmell`, and `GritMax`.
- Why secondary values do not match the values shown in-game.
- Whether maximum values should ever be edited directly.
- Whether absent secondary parameters may safely be inserted.
- Which values are recalculated from equipment, skills, hideout bonuses, or
  buffs when loading.
- Item-record flags, nested item collections, structural inventory resizing,
  equipment references, and type-specific item payload meanings.
- The layouts of skills, addictions, and timers.
