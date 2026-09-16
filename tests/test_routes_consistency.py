from typing import get_args
from server.domain import ROUTES
from server.router import Route


def test_route_literal_matches_domain_routes():
    assert tuple(get_args(Route)) == tuple(ROUTES)
