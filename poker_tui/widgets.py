from typing import List, Optional

try:
    from textual.app import ComposeResult
    from textual.containers import Container, Horizontal
    from textual.events import Key
    from textual.message import Message
    from textual.widgets import Button, Input, Static
    TEXTUAL_AVAILABLE = True
except ImportError:
    # Dummy shims para testing e inspección si Textual no está en el environment actual
    TEXTUAL_AVAILABLE = False
    class Message:
        pass
    class Input:
        def __init__(self, *args, **kwargs):
            self.value = kwargs.get("value", "")
            self.placeholder = kwargs.get("placeholder", "")
            self.id = kwargs.get("id", "")
    class Container:
        pass
    class Horizontal:
        pass
    class Static:
        def __init__(self, *args, **kwargs):
            self.id = kwargs.get("id", "")
    class Button:
        pass
    class Key:
        key: str

# 1. En la UI el usuario escribe:
#    e = Espadas (♠)
#    c = Corazones (♥)
#    d = Diamantes (♦)
#    t = Trébol (♣)
# 2. El motor backend (eval7/models) usa la convención universal de Texas Hold'em:
#    s = Spades (♠)
#    h = Hearts (♥)
#    d = Diamonds (♦)
#    c = Clubs (♣)

# Mapeo de lo que escribe el usuario en la UI -> código canónico del motor (s, h, d, c)
INPUT_TO_CANONICAL = {
    # Español
    "e": "s",  # Espadas   -> s (Spades)
    "c": "h",  # Corazones -> h (Hearts)
    "d": "d",  # Diamantes -> d (Diamonds)
    "t": "c",  # Trébol    -> c (Clubs)
    # Inglés (como alternativa)
    "s": "s",  # Spades
    "h": "h",  # Hearts
}

# Mapeo desde el código canónico del motor (s, h, d, c) hacia los símbolos gráficos
CANONICAL_TO_SYMBOL = {
    "s": "♠",  # Espadas
    "h": "♥",  # Corazones
    "d": "♦",  # Diamantes
    "c": "♣",  # Trébol
}

VALID_RANKS = set("23456789TJQKA")
VALID_SUIT_KEYS = set(INPUT_TO_CANONICAL.keys())
VALID_SUITS = VALID_SUIT_KEYS


def format_card_visual(card: str) -> str:
    """
    Convierte una carta ('3c', '3s', 'Ah', 'Kd') o ('3t', '3e')
    a su representación visual con símbolos Unicode y colores.
    
    Regla:
    - Si el segundo carácter es 'c', 'h' o '♥': Corazones (rojo ♥)
    - Si el segundo carácter es 't', '♣': Trébol (blanco ♣)
    - Si el segundo carácter es 'd', '♦': Diamantes (rojo ♦)
    - Si el segundo carácter es 'e', 's', '♠': Espadas (blanco ♠)
    """
    if len(card) != 2:
        return "[dim]--[/dim]"
    rank = card[0].upper()
    suit_char = card[1].lower()

    if suit_char in ("c", "h"):
        return f"[bold red]{rank}♥[/bold red]"
    elif suit_char in ("t",):
        return f"[bold white]{rank}♣[/bold white]"
    elif suit_char in ("d",):
        return f"[bold red]{rank}♦[/bold red]"
    elif suit_char in ("e", "s"):
        return f"[bold white]{rank}♠[/bold white]"
    else:
        return f"[bold white]{rank}{suit_char}[/bold white]"


class CardInput(Input):
    """
    Input especializado de 2 caracteres para cartas de póker (ej. 'As', 'Kd').
    Normaliza automáticamente y emite `CardInput.Completed` al tener 2 caracteres válidos
    para permitir el auto-avance del foco sin presionar Enter.
    """

    class Completed(Message):
        def __init__(self, slot_index: int, card_code: str, input_id: str) -> None:
            super().__init__()
            self.slot_index = slot_index
            self.card_code = card_code
            self.input_id = input_id

    class BackspaceOnEmpty(Message):
        def __init__(self, slot_index: int) -> None:
            super().__init__()
            self.slot_index = slot_index

    def __init__(self, slot_index: int, placeholder: str = "--", **kwargs) -> None:
        super().__init__(placeholder=placeholder, max_length=2, **kwargs)
        self.slot_index = slot_index
        self._suppress_event = False

    def on_key(self, event: Key) -> None:
        if event.key == "backspace" and self.value == "":
            if hasattr(event, "stop"):
                event.stop()
            self.post_message(self.BackspaceOnEmpty(self.slot_index))

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._suppress_event:
            return

        raw = event.value.strip()
        if not raw:
            self.post_message(self.Completed(self.slot_index, "", self.id or ""))
            return

        # 1. Validar Rank (primer carácter)
        r = raw[0].upper()
        if r not in VALID_RANKS:
            self._set_clean_value("")
            return

        if len(raw) == 1:
            if raw != r:
                self._set_clean_value(r)
            return

        # 2. Validar Suit (segundo carácter)
        s_raw = raw[1].lower()
        if s_raw not in VALID_SUIT_KEYS:
            self._set_clean_value(r)
            return

        normalized = f"{r}{s_raw}"
        if raw != normalized:
            self._set_clean_value(normalized)

        # Emitir mensaje para auto-avance
        self.post_message(self.Completed(self.slot_index, normalized, self.id or ""))

    def _set_clean_value(self, val: str) -> None:
        self._suppress_event = True
        self.value = val
        if hasattr(self, "cursor_position"):
            self.cursor_position = len(val)
        self._suppress_event = False


class FinanceInput(Input):
    """
    Input especializado para valores monetarios (Pot, To Call).
    Solo permite dígitos numéricos y emite eventos limpios de cambio.
    """

    class ValueCommitted(Message):
        def __init__(self, input_id: str, value: int) -> None:
            super().__init__()
            self.input_id = input_id
            self.value = value

    def __init__(self, placeholder: str = "0", **kwargs) -> None:
        super().__init__(placeholder=placeholder, max_length=8, **kwargs)
        self._suppress_event = False

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._suppress_event:
            return
        raw = event.value.strip()
        if not raw:
            self.post_message(self.ValueCommitted(self.id or "", 0))
            return

        digits = "".join([c for c in raw if c.isdigit()])
        if digits != raw:
            self._suppress_event = True
            self.value = digits
            if hasattr(self, "cursor_position"):
                self.cursor_position = len(digits)
            self._suppress_event = False

        numeric_val = int(digits) if digits else 0
        self.post_message(self.ValueCommitted(self.id or "", numeric_val))


class ToggleGroup(Horizontal):
    """
    Selector visual tipo Toggle para Posiciones o Conteo de Jugadores Activos.
    No captura foco por tabulación para mantener el flujo de teclado en los inputs.
    """
    can_focus_children = False

    def __init__(self, group_id: str, options: List[str], initial_selected: str, **kwargs) -> None:
        super().__init__(id=group_id, **kwargs)
        self.group_id = group_id
        self.options = options
        self.current_value = initial_selected

    def set_selected(self, option_val: str) -> None:
        self.current_value = option_val
        for opt in self.options:
            btn = self.query_one(f"#btn_{self.group_id}_{opt}", Button)
            if opt == option_val:
                btn.add_class("-selected")
            else:
                btn.remove_class("-selected")


class DecisionBanner(Container):
    """
    Panel Gigante y Reactivo de Decisión (Cerebro).
    Muestra FOLD en rojo, CALL en verde y RAISE en amarillo parpadeante.
    """

    def update_decision(
        self,
        action: str,
        amount: int = 0,
        equity: float = 0.0,
        pot_odds: float = 0.0,
        latency_ms: float = 0.0,
        reasoning: str = "",
    ) -> None:
        headline = self.query_one("#decision_headline", Static)
        submetrics = self.query_one("#decision_submetrics", Static)

        # Limpiar clases anteriores
        self.remove_class("state-idle", "state-computing", "state-fold", "state-call", "state-raise")

        action_upper = action.upper()
        if action_upper == "RAISE" or action_upper == "ALL_IN":
            self.add_class("state-raise")
            headline.update(f"★ {action_upper} {amount} ★")
        elif action_upper in ("CALL", "CHECK"):
            self.add_class("state-call")
            text = f"✔ {action_upper}" + (f" {amount}" if amount > 0 else "")
            headline.update(text)
        elif action_upper == "FOLD":
            self.add_class("state-fold")
            headline.update("✖ FOLD")
        elif action_upper == "COMPUTING":
            self.add_class("state-computing")
            headline.update("⏳ CALCULANDO...")
        else:
            self.add_class("state-idle")
            headline.update("ESPERANDO INPUT")

        # Submétricas
        if action_upper in ("RAISE", "ALL_IN", "CALL", "CHECK", "FOLD"):
            submetrics.update(
                f"Equity: {equity*100:.1f}% | Pot Odds: {pot_odds*100:.1f}% | Latencia: {latency_ms:.1f}ms\n{reasoning}"
            )
        else:
            submetrics.update("Presiona [b]Ctrl+Enter[/b] o [b]F9[/b] para consultar")
