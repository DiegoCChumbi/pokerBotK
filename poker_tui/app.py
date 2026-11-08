import json
from typing import Any, Dict, List, Optional

try:
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Grid, Horizontal, Vertical
    from textual.reactive import reactive
    from textual.widgets import Button, Footer, Header, Input, Label, Static
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False
    class App:
        pass
    class ComposeResult:
        pass
    def reactive(default_val):
        return default_val
    def work(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator
    class Binding:
        def __init__(self, *args, **kwargs):
            pass

from .backend_bridge import (
    BrainBackendProtocol,
    BrainResponse,
    DummyBrainBackend,
    LivePokerBotBridge,
)
from .widgets import (
    CardInput,
    DecisionBanner,
    FinanceInput,
    ToggleGroup,
    format_card_visual,
)

POSITIONS = ["SB", "BB", "UTG", "MP", "CO", "BTN"]
ACTIVE_COUNTS = ["2", "3", "4", "5", "6"]


class PokerCockpitApp(App):
    """
    TUI de alta velocidad para ingreso manual de estado de mesa y consulta asíncrona.
    """
    CSS_PATH = "styles.tcss"
    TITLE = "POKERFIGHT COCKPIT TUI"

    BINDINGS = [
        # Disparo Asíncrono del Motor ("Cerebro")
        Binding("ctrl+enter", "trigger_brain", "Enviar al Cerebro", priority=True),
        Binding("ctrl+j", "trigger_brain", "Enviar al Cerebro", show=False, priority=True),
        Binding("f9", "trigger_brain", "Enviar al Cerebro", priority=True),

        # Ciclo de Mano y Foco
        Binding("ctrl+r", "reset_hand", "Nueva Mano (Ctrl+R)", priority=True),
        Binding("ctrl+l", "reset_hand", "Limpiar (Ctrl+L)", priority=True),
        Binding("c", "clear_if_not_input", "Limpiar Mesa (C)", show=False, priority=False),
        Binding("escape", "focus_first_card", "Mano (Esc)", priority=True),
        Binding("ctrl+f", "focus_flop", "Flop", priority=True),
        Binding("ctrl+t", "focus_turn", "Turn", priority=True),
        Binding("ctrl+v", "focus_river", "River", priority=True),
        Binding("ctrl+p", "focus_pot", "Ir a Pot", priority=True),
        Binding("ctrl+s", "focus_stack", "Ir a Fichas", priority=True),

        # Posiciones (F1-F6 o tecla rápida Ctrl+D para rotar posición)
        Binding("ctrl+d", "rotate_pos", "Rotar Posición (Ctrl+D)", priority=True),
        Binding("f1", "set_pos('SB')", "SB", priority=True),
        Binding("f2", "set_pos('BB')", "BB", priority=True),
        Binding("f3", "set_pos('UTG')", "UTG", priority=True),
        Binding("f4", "set_pos('MP')", "MP", priority=True),
        Binding("f5", "set_pos('CO')", "CO", priority=True),
        Binding("f6", "set_pos('BTN')", "BTN", priority=True),

        # Jugadores Activos
        # 1. Con teclas de función F7 a F11 (para no colisionar con tmux):
        Binding("f7", "set_active(2)", "2 Jug (F7)", priority=True),
        Binding("f8", "set_active(3)", "3 Jug (F8)", priority=True),
        Binding("f10", "set_active(4)", "4 Jug (F10)", priority=True),
        Binding("f11", "set_active(5)", "5 Jug (F11)", priority=True),
        Binding("f12", "set_active(6)", "6 Jug (F12)", priority=True),
        # 2. Con Ctrl+a para ciclar rápidamente: 6 -> 5 -> 4 -> 3 -> 2 -> 6
        Binding("ctrl+a", "cycle_active", "Ciclar Activos (Ctrl+A)", priority=True),
        # 3. Mantener Alt+2..6 por compatibilidad fuera de tmux (ocultos del footer):
        Binding("alt+2", "set_active(2)", "2P", show=False, priority=True),
        Binding("alt+3", "set_active(3)", "3P", show=False, priority=True),
        Binding("alt+4", "set_active(4)", "4P", show=False, priority=True),
        Binding("alt+5", "set_active(5)", "5P", show=False, priority=True),
        Binding("alt+6", "set_active(6)", "6P", show=False, priority=True),
    ]

    # Estado Reactivo (Entregable 2)
    hand_cards: reactive[List[str]] = reactive(lambda: ["", ""])
    board_cards: reactive[List[str]] = reactive(lambda: ["", "", "", "", ""])
    stack_size: reactive[int] = reactive(1000)
    pot_size: reactive[int] = reactive(0)
    to_call: reactive[int] = reactive(0)
    hero_pos: reactive[str] = reactive("BTN")
    active_players: reactive[int] = reactive(6)
    brain_status: reactive[str] = reactive("IDLE")
    last_response: reactive[Optional[BrainResponse]] = reactive(None)

    def __init__(self, backend: Optional[BrainBackendProtocol] = None, **kwargs) -> None:
        if TEXTUAL_AVAILABLE:
            super().__init__(**kwargs)
        self.backend: BrainBackendProtocol = backend or DummyBrainBackend()

    @property
    def current_payload(self) -> Dict[str, Any]:
        """Ensambla en tiempo real el diccionario exacto requerido."""
        return {
            "hand": [c for c in self.hand_cards if len(c) == 2],
            "board": [c for c in self.board_cards if len(c) == 2],
            "stack": self.stack_size,
            "pot": self.pot_size,
            "to_call": self.to_call,
            "active": self.active_players,
            "pos": self.hero_pos,
        }

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, id="header_bar")

        with Container(id="main_cockpit_grid"):
            # ZONA 1: CARTAS
            with Vertical(id="cards_zone"):
                yield Label("1. CARTAS (Espadas=e/s, Corazones=c/h, Diamantes=d, Trébol=t)", classes="zone-title")
                with Horizontal(classes="cards-row"):
                    yield Label("Tu Mano: ", classes="row-lbl")
                    yield CardInput(slot_index=0, id="hc_1")
                    yield CardInput(slot_index=1, id="hc_2")
                    yield Static("[dim]--  --[/dim]", id="hand_visual_preview", classes="visual-card-preview")
                with Horizontal(classes="cards-row"):
                    yield Label("Mesa:    ", classes="row-lbl")
                    yield CardInput(slot_index=2, id="bc_1")
                    yield CardInput(slot_index=3, id="bc_2")
                    yield CardInput(slot_index=4, id="bc_3")
                    yield CardInput(slot_index=5, id="bc_4")
                    yield CardInput(slot_index=6, id="bc_5")
                    yield Static("[dim]--  --  --  --  --[/dim]", id="board_visual_preview", classes="visual-card-preview")

            # ZONA 2: FINANZAS
            with Vertical(id="finance_zone"):
                yield Label("2. FINANZAS (Fichas, Pot & To Call)", classes="zone-title")
                with Horizontal(classes="finance-row"):
                    yield Label("Tus Fichas: ", classes="row-lbl")
                    yield FinanceInput(id="stack_input", placeholder="1000")
                with Horizontal(classes="finance-row"):
                    yield Label("Pozo Total: ", classes="row-lbl")
                    yield FinanceInput(id="pot_input", placeholder="0")
                with Horizontal(classes="finance-row"):
                    yield Label("A Igualar:  ", classes="row-lbl")
                    yield FinanceInput(id="call_input", placeholder="0")
                yield Static("Pot Odds: 0.0%", id="live_pot_odds_preview")

            # ZONA 3: CONTEXTO
            with Vertical(id="context_zone"):
                yield Label("3. CONTEXTO DE MESA", classes="zone-title")
                with Horizontal(id="pos_group", classes="toggle-row"):
                    yield Label("Posición (Ctrl+D / F1-F6):", classes="row-lbl")
                    for p in POSITIONS:
                        btn = Button(p, id=f"btn_pos_group_{p}", classes="toggle-btn")
                        if p == "BTN":
                            btn.add_class("-selected")
                        yield btn
                with Horizontal(id="active_group", classes="toggle-row"):
                    yield Label("Activos (Ctrl+A / F7-F12):", classes="row-lbl")
                    for a in ACTIVE_COUNTS:
                        btn = Button(f"{a}P", id=f"btn_active_group_{a}", classes="toggle-btn")
                        if a == "6":
                            btn.add_class("-selected")
                        yield btn
                yield Static(id="json_payload_preview")

            # ZONA 4: PANEL DE DECISIÓN (CEREBRO)
            with DecisionBanner(id="decision_banner", classes="state-idle"):
                yield Static("ESPERANDO INPUT", id="decision_headline")
                yield Static("Presiona Ctrl+Enter o F9 para evaluar", id="decision_submetrics")

        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#hc_1", CardInput).focus()
        self._refresh_payload_preview()

    # --- AUTO-AVANCE Y GESTIÓN DE EVENTOS DE TECLADO ---

    def on_card_input_completed(self, message: CardInput.Completed) -> None:
        """Captura el evento del input y actualiza el array de cartas reactivo."""
        slot = message.slot_index
        card = message.card_code

        if slot < 2:
            new_hand = list(self.hand_cards)
            new_hand[slot] = card
            self.hand_cards = new_hand
        else:
            board_idx = slot - 2
            new_board = list(self.board_cards)
            new_board[board_idx] = card
            self.board_cards = new_board

        self._refresh_payload_preview()

        # Cadena de Salto de Foco Inteligente
        if card != "":  # Solo avanza si se completó una carta
            next_focus_id = self._get_next_focus_slot(slot)
            if next_focus_id:
                target_widget = self.query_one(f"#{next_focus_id}")
                if target_widget:
                    target_widget.focus()

    def on_card_input_backspace_on_empty(self, message: CardInput.BackspaceOnEmpty) -> None:
        """Si presiona Backspace en un slot vacío, retrocede al anterior."""
        slot = message.slot_index
        prev_focus_id = self._get_prev_focus_slot(slot)
        if prev_focus_id:
            target_widget = self.query_one(f"#{prev_focus_id}", CardInput)
            if target_widget:
                target_widget.focus()
                target_widget.value = ""

    def on_finance_input_value_committed(self, message: FinanceInput.ValueCommitted) -> None:
        if message.input_id == "stack_input":
            self.stack_size = message.value
        elif message.input_id == "pot_input":
            self.pot_size = message.value
        elif message.input_id == "call_input":
            self.to_call = message.value
        self._refresh_pot_odds_preview()
        self._refresh_payload_preview()

    def _get_next_focus_slot(self, slot: int) -> Optional[str]:
        # Flujo natural de cartas:
        # Slot 0 (Hand 1) -> Slot 1 (Hand 2) -> Slot 2 (Flop 1) -> Slot 3 (Flop 2) -> Slot 4 (Flop 3) -> Slot 5 (Turn) -> Slot 6 (River) -> Pot
        chain = ["hc_1", "hc_2", "bc_1", "bc_2", "bc_3", "bc_4", "bc_5", "pot_input"]
        if slot + 1 < len(chain):
            return chain[slot + 1]
        return None

    def _get_prev_focus_slot(self, slot: int) -> Optional[str]:
        chain = ["hc_1", "hc_2", "bc_1", "bc_2", "bc_3", "bc_4", "bc_5"]
        if slot > 0 and slot < len(chain):
            return chain[slot - 1]
        return None

    def _refresh_pot_odds_preview(self) -> None:
        if not TEXTUAL_AVAILABLE:
            return
        total = self.pot_size + self.to_call
        pct = (self.to_call / total * 100.0) if (total > 0 and self.to_call > 0) else 0.0
        odds_lbl = self.query_one("#live_pot_odds_preview", Static)
        odds_lbl.update(f"Pot Odds: {pct:.1f}% (Req. Equity para pagar)")

    def _refresh_payload_preview(self) -> None:
        if not TEXTUAL_AVAILABLE:
            return
        # Actualizar visualización con símbolos
        hand_vis = "  ".join([format_card_visual(c) for c in self.hand_cards])
        board_vis = "  ".join([format_card_visual(c) for c in self.board_cards])
        self.query_one("#hand_visual_preview", Static).update(hand_vis)
        self.query_one("#board_visual_preview", Static).update(board_vis)

        preview = self.query_one("#json_payload_preview", Static)
        preview.update(f"Payload al Cerebro: {json.dumps(self.current_payload)}")

    # --- WATCHERS REACTIVOS ---

    def watch_last_response(self, response: Optional[BrainResponse]) -> None:
        if not TEXTUAL_AVAILABLE or response is None:
            return
        banner = self.query_one("#decision_banner", DecisionBanner)
        banner.update_decision(
            action=response.action,
            amount=response.amount,
            equity=response.equity,
            pot_odds=response.pot_odds,
            latency_ms=response.latency_ms,
            reasoning=response.reasoning,
        )

    # --- ACCIONES DE ATAJOS GLOBALES ---

    def action_trigger_brain(self) -> None:
        """Disparo asíncrono no bloqueante hacia el motor de decisiones."""
        self.dispatch_to_brain(self.current_payload)

    @work(exclusive=True)
    async def dispatch_to_brain(self, payload: Dict[str, Any]) -> None:
        if TEXTUAL_AVAILABLE:
            banner = self.query_one("#decision_banner", DecisionBanner)
            banner.update_decision(action="COMPUTING")

        response = await self.backend.evaluate_table_state(payload)
        self.last_response = response

    def action_reset_hand(self) -> None:
        """Limpia cartas y pozo, manteniendo la rotación fluida."""
        self.hand_cards = ["", ""]
        self.board_cards = ["", "", "", "", ""]
        self.pot_size = 0
        self.to_call = 0
        if TEXTUAL_AVAILABLE:
            for cid in ["hc_1", "hc_2", "bc_1", "bc_2", "bc_3", "bc_4", "bc_5"]:
                self.query_one(f"#{cid}", CardInput).value = ""
            self.query_one("#pot_input", FinanceInput).value = ""
            self.query_one("#call_input", FinanceInput).value = ""
            banner = self.query_one("#decision_banner", DecisionBanner)
            banner.update_decision(action="IDLE")
            self.query_one("#hc_1", CardInput).focus()
            self._refresh_payload_preview()

    def action_focus_first_card(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#hc_1", CardInput).focus()

    def action_focus_flop(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#bc_1", CardInput).focus()

    def action_focus_turn(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#bc_4", CardInput).focus()

    def action_focus_river(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#bc_5", CardInput).focus()

    def action_focus_pot(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#pot_input", FinanceInput).focus()

    def action_focus_stack(self) -> None:
        if TEXTUAL_AVAILABLE:
            self.query_one("#stack_input", FinanceInput).focus()

    def action_set_pos(self, pos: str) -> None:
        self.hero_pos = pos
        if TEXTUAL_AVAILABLE:
            for p in POSITIONS:
                btn = self.query_one(f"#btn_pos_group_{p}", Button)
                if p == pos:
                    btn.add_class("-selected")
                else:
                    btn.remove_class("-selected")
            self._refresh_payload_preview()

    def action_rotate_pos(self) -> None:
        """Rota la posición al siguiente asiento hacia la izquierda en la mesa."""
        # Orden natural de rotación de la ficha D: BTN -> SB -> BB -> UTG -> MP -> CO -> BTN
        order = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
        try:
            curr_idx = order.index(self.hero_pos)
            next_pos = order[(curr_idx + 1) % len(order)]
        except ValueError:
            next_pos = "BTN"
        self.action_set_pos(next_pos)

    def action_set_active(self, count: int) -> None:
        self.active_players = count
        if TEXTUAL_AVAILABLE:
            for a in ACTIVE_COUNTS:
                btn = self.query_one(f"#btn_active_group_{a}", Button)
                if a == str(count):
                    btn.add_class("-selected")
                else:
                    btn.remove_class("-selected")
            self._refresh_payload_preview()

    def action_cycle_active(self) -> None:
        """Cicla los jugadores activos hacia abajo (6 -> 5 -> 4 -> 3 -> 2 -> 6)."""
        counts = [6, 5, 4, 3, 2]
        curr = self.active_players
        try:
            curr_idx = counts.index(curr)
            next_count = counts[(curr_idx + 1) % len(counts)]
        except ValueError:
            next_count = 6
        self.action_set_active(next_count)
