from app.models.game_state import (
    BuildCategoryCounts,
    BuildOperatingSystem,
    BuildProfile,
    BuildQuickhackLoadout,
    BuildResources,
    DeepCyberdeckState,
    GameState,
)


# Exact, documented TweakDB IDs only. Unknown records are intentionally not guessed.
QUICKHACK_CATALOG: dict[str, str] = {
    "Items.GrenadeExplodeLvl4Program": "offensive",
    "Items.PingLvl4Program": "recon",
    "Items.ContagionLvl4Program": "offensive",
    "Items.OverheatLvl4Program": "offensive",
    "Items.BrainMeltLvl4Program": "offensive",
    "Items.SuicideLvl4Program": "offensive",
    "Items.WeaponMalfunctionLvl4Program": "control",
}

LIMITATIONS = [
    "attributes_unavailable",
    "perks_unavailable",
    "full_cyberware_unavailable",
    "full_weapon_loadout_unavailable",
]


class BuildAwareness:
    """Pure, deterministic inference over already-confirmed telemetry."""

    @staticmethod
    def _clamp(value: float) -> float:
        return round(max(0.0, min(1.0, value)), 2)

    @staticmethod
    def _player_ready(state: GameState) -> bool:
        deep = state.deep_game_state
        if deep is None or deep.player is None:
            return False
        player = deep.player
        return player.entity_available is True and player.session_available is True and player.is_pre_game is not True

    @staticmethod
    def _capacity_consistent(deck: DeepCyberdeckState, installed: int) -> bool:
        capacity = deck.program_capacity
        if capacity is None or capacity.total is None:
            return False
        if capacity.used is not None and capacity.used != installed:
            return False
        if capacity.empty is not None and capacity.used is not None and capacity.used + capacity.empty != capacity.total:
            return False
        return installed <= capacity.total

    def build(self, state: GameState) -> BuildProfile | None:
        deep = state.deep_game_state
        if deep is None or not self._player_ready(state):
            return None

        deck = deep.cyberdeck
        resources = BuildResources(
            health_maximum=deep.stat_pools.maximum_health if deep.stat_pools else None,
            ram_maximum=deep.stat_pools.maximum_memory if deep.stat_pools else None,
            armor=deep.stats.armor if deep.stats else None,
        )
        weapon_id = deep.weapon.record_id if deep.weapon else None
        if deck is None:
            identity_confirmed = deep.capabilities is not None and deep.capabilities.cyberdeck_identity is True
            confidence = 0.8 if identity_confirmed else 0.35
            return BuildProfile(
                resources=resources,
                current_weapon_record_id=weapon_id,
                summary_key="no_cyberdeck",
                confidence=confidence,
                evidence=["cyberdeck_absence_observed"] if identity_confirmed else [],
                limitations=(LIMITATIONS + ([] if identity_confirmed else ["cyberdeck_identity_unavailable"]))[:12],
            )

        identity_valid = bool(deck.record_id and deck.tags and "Cyberdeck" in deck.tags)
        programs = deck.programs
        installed = len(programs) if programs is not None else None
        categories = BuildCategoryCounts()
        if programs is not None:
            for program in programs[:8]:
                category = QUICKHACK_CATALOG.get(program.record_id, "unknown")
                setattr(categories, category, getattr(categories, category) + 1)
        known = None if installed is None else installed - categories.unknown
        unknown = None if installed is None else categories.unknown
        classified_counts = {
            "offensive": categories.offensive,
            "control": categories.control,
            "recon": categories.recon,
            "utility": categories.utility,
        }
        dominant = None
        style = "cyberdeck_unknown" if identity_valid else "unknown"
        if known and identity_valid:
            category, count = max(classified_counts.items(), key=lambda item: (item[1], item[0]))
            if count / known >= 0.6:
                dominant = category
                style = f"{category}_netrunner" if category != "utility" else "mixed_netrunner"
            else:
                style = "mixed_netrunner"

        total = deck.program_capacity.total if deck.program_capacity else None
        fill = round(installed / total * 100, 1) if installed is not None and total else None
        capacity_ok = installed is not None and self._capacity_consistent(deck, installed)
        known_ratio = known / installed if installed else 0.0
        score = 0.25 if identity_valid else 0.05
        score += 0.15 if capacity_ok else 0.0
        score += 0.10 if programs is not None else 0.0
        score += 0.30 * known_ratio
        score += 0.10 if resources.ram_maximum is not None else 0.0
        score += 0.10 if programs is not None and len({p.slot_id for p in programs}) == installed else 0.0
        if deck.truncated is True:
            score -= 0.15
        if installed is not None and deck.program_capacity and deck.program_capacity.used is not None and deck.program_capacity.used != installed:
            score -= 0.15
        confidence = self._clamp(score)

        evidence = ["cyberdeck_tag_confirmed"] if identity_valid else []
        if installed is not None:
            evidence.append(f"{installed}_installed_quickhacks")
        for category in ("offensive", "control", "recon", "utility"):
            count = getattr(categories, category)
            if count:
                evidence.append(f"{count}_known_{category}_quickhacks")
        if resources.ram_maximum is not None:
            evidence.append("maximum_ram_available")
        limitations = list(LIMITATIONS)
        if not identity_valid:
            limitations.append("cyberdeck_identity_partial")
        if programs is None:
            limitations.append("quickhack_programs_unavailable")
        if unknown:
            limitations.append("unknown_quickhack_records")
        if total is None:
            limitations.append("cyberdeck_capacity_unavailable")
        if not capacity_ok and total is not None:
            limitations.append("cyberdeck_capacity_inconsistent")
        if deck.truncated is True:
            limitations.append("quickhack_programs_truncated")
        if resources.ram_maximum is None:
            limitations.append("maximum_ram_unavailable")

        return BuildProfile(
            operating_system=BuildOperatingSystem(kind="cyberdeck", record_id=deck.record_id, quality=deck.quality, iconic=deck.iconic),
            quickhack_loadout=BuildQuickhackLoadout(
                installed=installed, capacity=total, fill_percent=fill,
                known_programs=known, unknown_programs=unknown, categories=categories,
                dominant_category=dominant, style=style, confidence=confidence,
            ),
            resources=resources,
            current_weapon_record_id=weapon_id,
            summary_key=style,
            confidence=confidence,
            evidence=evidence[:12],
            limitations=limitations[:12],
        )

    def enrich(self, state: GameState) -> GameState:
        if state.deep_game_state is not None:
            state.deep_game_state.build_profile = self.build(state)
        return state
