from assistant_ia.limits import ConcurrencyLimiter, RateLimiter


def test_rate_limiter_allows_up_to_the_maximum_then_refuses():
    t = [0.0]
    limiteur = RateLimiter(maximum=3, fenetre_s=60, now_fn=lambda: t[0])

    assert [limiteur.autorise("1.2.3.4") for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_frees_a_slot_once_the_window_has_passed():
    t = [0.0]
    limiteur = RateLimiter(maximum=1, fenetre_s=60, now_fn=lambda: t[0])
    assert limiteur.autorise("a") is True
    assert limiteur.autorise("a") is False
    t[0] = 61.0
    assert limiteur.autorise("a") is True


def test_rate_limiter_counts_each_visitor_separately():
    limiteur = RateLimiter(maximum=1, fenetre_s=60, now_fn=lambda: 0.0)
    assert limiteur.autorise("a") is True
    assert limiteur.autorise("b") is True
    assert limiteur.autorise("a") is False


def test_concurrency_limiter_refuses_without_waiting_when_full():
    limiteur = ConcurrencyLimiter(maximum=2)
    assert limiteur.tente() is True
    assert limiteur.tente() is True
    assert limiteur.tente() is False
    limiteur.libere()
    assert limiteur.tente() is True
    assert limiteur.en_cours == 2


def test_concurrency_limiter_never_goes_negative():
    limiteur = ConcurrencyLimiter(maximum=1)
    limiteur.libere()
    assert limiteur.en_cours == 0
