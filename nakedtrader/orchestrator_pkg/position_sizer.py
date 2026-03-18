"""nakedtrader.orchestrator.position_sizer — Kelly-gebaseerde position sizing."""


def calculate_position_size(
    signal,
    config,
    sizer,
    adaptive,
    risk_mgr,
    current_risk,
    kelly_correction: float,
) -> float:
    """Bereken positiegrootte via Kelly + alle correcties.

    Standalone functie zodat subklassen eenvoudig kunnen overschrijven via
    ``_calculate_size()`` op BaseOrchestrator.
    """
    sid = signal.strategy_id or ""
    drawdown = risk_mgr.drawdown_pct

    # Adaptive win-rate (als voldoende data)
    win_prob = signal.win_probability
    if sid:
        state = adaptive.get_state(sid)
        if len(state.recent_trades) >= 10:
            win_prob = adaptive.get_rolling_win_rate(sid)

    kelly_frac = sizer.calculate(win_prob, signal.expected_win_pct, signal.expected_loss_pct)

    # Macro risk Kelly-reductie
    if current_risk:
        kelly_frac *= current_risk.kelly_mult

    # Orchestrator-specifieke correctie
    kelly_frac *= kelly_correction

    # R4: Drawdown caution (>5%) — halveer
    if drawdown >= 0.05:
        kelly_frac *= 0.5

    size_eur = config.total_capital * kelly_frac

    # Per-strategie budget check
    if sid and not risk_mgr.allow_trade(sid, size_eur):
        return 0.0

    return size_eur
