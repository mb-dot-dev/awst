"""The awst Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import boto3
from textual.app import App

from awst.aws.cloudformation import CloudFormationGateway
from awst.screens.home import HomeScreen

if TYPE_CHECKING:
    from awst.screens.stacks import StackGateway


class AwstApp(App[None]):
    """AWS console terminal UI."""

    def __init__(self: Self, cloudformation_gateway: StackGateway | None = None) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway

    @property
    def cloudformation_gateway(self: Self) -> StackGateway:
        """The CloudFormation gateway, built on first use from the default credential chain."""
        if self._cloudformation_gateway is None:
            session = boto3.Session()
            self._cloudformation_gateway = CloudFormationGateway(session.client("cloudformation"))
        return self._cloudformation_gateway

    def on_mount(self: Self) -> None:
        self.push_screen(HomeScreen())
