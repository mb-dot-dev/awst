"""Tests for the S3 object list screen."""

import contextlib
import threading
from typing import TYPE_CHECKING, Self

import pytest
from textual.app import App
from textual.widgets import DataTable, Static
from textual.worker import WorkerCancelled, WorkerFailed

from awst.aws.models import AwsError, ObjectPage
from awst.screens.confirm import ConfirmScreen
from awst.screens.objects import ObjectListScreen
from tests.fakes import FakeS3Gateway, make_object

if TYPE_CHECKING:
    from textual.pilot import Pilot


class ObjectScreenApp(App[None]):
    """Minimal harness that opens the object list for bucket "assets" directly."""

    def __init__(self: Self, gateway: FakeS3Gateway, prefix: str = "") -> None:
        super().__init__()
        self.gateway = gateway
        self.prefix = prefix

    def on_mount(self: Self) -> None:
        self.push_screen(ObjectListScreen(self.gateway, "assets", "eu-west-1", self.prefix))


async def _settle(app: App[None]) -> None:
    await app.workers.wait_for_complete()


@pytest.fixture
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record notifications instead of rendering toasts."""
    messages: list[str] = []

    def record_notify(self: App[None], message: str, **kwargs: object) -> None:
        messages.append(message)

    monkeypatch.setattr(App, "notify", record_notify)
    return messages


async def _until_back_on_list(app: ObjectScreenApp, pilot: Pilot[None]) -> None:
    """Let the delete modal finish and pop, tolerating cancelled/failed workers."""
    for _ in range(100):
        with contextlib.suppress(WorkerFailed, WorkerCancelled):
            await app.workers.wait_for_complete()
        await pilot.pause()
        if isinstance(app.screen, ObjectListScreen):
            return
    pytest.fail("never returned to the object list")


@pytest.mark.asyncio
async def test_renders_folders_first_with_sizes_and_blank_folder_cells() -> None:
    page = ObjectPage(folders=("docs/",), objects=(make_object("readme.md", size=1536),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): page}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.row_count == 2
        assert table.get_row_at(0) == ["docs/", "", ""]
        assert table.get_row_at(1)[0] == "readme.md"
        assert table.get_row_at(1)[1] == "1.5 KB"
        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]


@pytest.mark.asyncio
async def test_names_are_relative_to_the_prefix() -> None:
    page = ObjectPage(
        folders=("docs/2026/",),
        objects=(make_object("docs/guide.md"),),
        continuation_token=None,
    )
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("docs/", None): page}), prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        table = app.screen.query_one(DataTable)

        assert table.get_row_at(0)[0] == "2026/"
        assert table.get_row_at(1)[0] == "guide.md"


@pytest.mark.asyncio
async def test_enter_on_folder_drills_down_and_escape_pops_back() -> None:
    root = ObjectPage(folders=("docs/",), objects=(), continuation_token=None)
    docs = ObjectPage(folders=(), objects=(make_object("docs/guide.md"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): root, ("docs/", None): docs}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        parent = app.screen

        await pilot.press("enter")
        await _settle(app)
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert app.screen is not parent
        assert app.gateway.object_calls[-1] == ("assets", "eu-west-1", "docs/", None)
        assert app.screen.query_one(DataTable).get_row_at(0)[0] == "guide.md"

        await pilot.press("escape")
        await pilot.pause()

        assert app.screen is parent


@pytest.mark.asyncio
async def test_enter_on_an_object_does_nothing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): page}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        screen = app.screen

        await pilot.press("enter")
        await pilot.pause()

        assert app.screen is screen
        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]


@pytest.mark.asyncio
async def test_m_loads_the_next_page_with_the_token() -> None:
    first = ObjectPage(folders=(), objects=(make_object("a.txt"), make_object("b.txt")), continuation_token="t1")
    second = ObjectPage(folders=(), objects=(make_object("c.txt"),), continuation_token=None)
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): first, ("", "t1"): second}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        assert str(app.screen.query_one("#count", Static).content) == "2+ objects"

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()

        assert app.gateway.object_calls[-1] == ("assets", "eu-west-1", "", "t1")
        assert app.screen.query_one(DataTable).row_count == 3
        assert str(app.screen.query_one("#count", Static).content) == "3 objects"


@pytest.mark.asyncio
async def test_initial_load_failure_shows_error_panel() -> None:
    app = ObjectScreenApp(FakeS3Gateway(objects_error=AwsError("access denied")))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        panel = app.screen.query_one("#error", Static)

        assert panel.display is True
        assert "access denied" in str(panel.content)


@pytest.mark.asyncio
async def test_sub_title_shows_bucket_and_prefix() -> None:
    app = ObjectScreenApp(FakeS3Gateway(), prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        assert app.screen.sub_title == "assets/docs/"


@pytest.mark.asyncio
async def test_refresh_during_in_flight_load_more_keeps_paging_consistent() -> None:
    first = ObjectPage(folders=(), objects=(make_object("a.txt"), make_object("b.txt")), continuation_token="t1")
    second = ObjectPage(folders=(), objects=(make_object("c.txt"),), continuation_token=None)
    gate = threading.Event()
    gateway = FakeS3Gateway(object_pages={("", None): first, ("", "t1"): second}, objects_gate=gate)
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("m")  # the zombie load-more worker blocks in the gateway on token "t1"
        await pilot.pause()

        await pilot.press("r")  # cancels the load-more; the first-page fetch completes and resets the token to "t1"
        with contextlib.suppress(WorkerCancelled, WorkerFailed):
            await app.workers.wait_for_complete()
        await pilot.pause()

        gate.set()  # release the zombie: without the guard it would overwrite the token with None
        with contextlib.suppress(WorkerCancelled, WorkerFailed):
            await app.workers.wait_for_complete()
        await pilot.pause(0.2)  # give the now-unblocked background thread time to run its (discarded) write

        assert app.screen.check_action("load_more", ()) is True

        await pilot.press("m")
        await _settle(app)
        await pilot.pause()

        assert app.gateway.object_calls[-1] == ("assets", "eu-west-1", "", "t1")


@pytest.mark.asyncio
async def test_filtering_does_not_fetch_remaining_pages() -> None:
    first = ObjectPage(folders=(), objects=(make_object("a.txt"), make_object("b.txt")), continuation_token="t1")
    app = ObjectScreenApp(FakeS3Gateway(object_pages={("", None): first}))

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("slash")
        await pilot.press(*"a")
        await _settle(app)
        await pilot.pause()

        assert app.gateway.object_calls == [("assets", "eu-west-1", "", None)]  # no auto-fetch of the next page
        assert app.screen.query_one(DataTable).row_count == 1


@pytest.mark.asyncio
async def test_d_on_an_object_confirms_then_deletes_that_key() -> None:
    page = ObjectPage(folders=(), objects=(make_object("docs/readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("docs/", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway, prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        question = str(app.screen.query_one("#question", Static).content)
        assert question == "Permanently delete docs/readme.md and all its versions?"

        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert gateway.deleted == [("object", "assets", "eu-west-1", "docs/readme.md")]


@pytest.mark.asyncio
async def test_d_on_a_folder_deletes_the_whole_prefix() -> None:
    page = ObjectPage(folders=("docs/2026/",), objects=(), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("docs/", None): page}, delete_batches=[4])
    app = ObjectScreenApp(gateway, prefix="docs/")

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        question = str(app.screen.query_one("#question", Static).content)
        assert question == "Permanently delete everything under docs/2026/, including all versions?"

        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert gateway.deleted == [("prefix", "assets", "eu-west-1", "docs/2026/")]


@pytest.mark.asyncio
async def test_declining_the_confirmation_deletes_nothing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        assert isinstance(app.screen, ObjectListScreen)
        assert gateway.deleted == []
        assert len(gateway.object_calls) == 1  # no refresh either


@pytest.mark.asyncio
async def test_d_with_no_rows_does_nothing() -> None:
    gateway = FakeS3Gateway(object_pages={})
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()
        screen = app.screen

        await pilot.press("d")
        await pilot.pause()

        assert app.screen is screen
        assert gateway.deleted == []


@pytest.mark.asyncio
async def test_finishing_a_delete_refreshes_the_listing() -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_batches=[1])
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)
        await _settle(app)
        await pilot.pause()

        assert gateway.object_calls == [
            ("assets", "eu-west-1", "", None),
            ("assets", "eu-west-1", "", None),
        ]


@pytest.mark.asyncio
async def test_delete_failure_toasts_and_returns_to_the_list(toasts: list[str]) -> None:
    page = ObjectPage(folders=(), objects=(make_object("readme.md"),), continuation_token=None)
    gateway = FakeS3Gateway(object_pages={("", None): page}, delete_error=AwsError("Access Denied"))
    app = ObjectScreenApp(gateway)

    async with app.run_test() as pilot:
        await _settle(app)
        await pilot.pause()

        await pilot.press("d")
        await pilot.pause()
        await pilot.press("y")
        await _until_back_on_list(app, pilot)

        assert "Access Denied" in toasts
