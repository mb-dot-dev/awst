"""The awst Textual application."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

import boto3
from textual.app import App

from awst.aws.cloudformation import CloudFormationGateway
from awst.aws.lambda_ import LambdaGateway
from awst.aws.s3 import S3Gateway
from awst.aws.sqs import SqsGateway
from awst.screens.home import HomeScreen

if TYPE_CHECKING:
    from awst.screens.buckets import BucketLister
    from awst.screens.functions import FunctionLister
    from awst.screens.queues import QueueLister
    from awst.screens.stacks import StackGateway


class AwstApp(App[None]):
    """AWS console terminal UI."""

    def __init__(
        self: Self,
        cloudformation_gateway: StackGateway | None = None,
        s3_gateway: BucketLister | None = None,
        lambda_gateway: FunctionLister | None = None,
        sqs_gateway: QueueLister | None = None,
    ) -> None:
        super().__init__()
        self._cloudformation_gateway = cloudformation_gateway
        self._s3_gateway = s3_gateway
        self._lambda_gateway = lambda_gateway
        self._sqs_gateway = sqs_gateway

    @property
    def cloudformation_gateway(self: Self) -> StackGateway:
        """The CloudFormation gateway, built on first use from the default credential chain."""
        if self._cloudformation_gateway is None:
            session = boto3.Session()
            self._cloudformation_gateway = CloudFormationGateway(session.client("cloudformation"))
        return self._cloudformation_gateway

    @property
    def s3_gateway(self: Self) -> BucketLister:
        """The S3 gateway, built on first use from the default credential chain."""
        if self._s3_gateway is None:
            session = boto3.Session()
            self._s3_gateway = S3Gateway(session.client("s3"))
        return self._s3_gateway

    @property
    def lambda_gateway(self: Self) -> FunctionLister:
        """The Lambda gateway, built on first use from the default credential chain."""
        if self._lambda_gateway is None:
            session = boto3.Session()
            self._lambda_gateway = LambdaGateway(session.client("lambda"))
        return self._lambda_gateway

    @property
    def sqs_gateway(self: Self) -> QueueLister:
        """The SQS gateway, built on first use from the default credential chain."""
        if self._sqs_gateway is None:
            session = boto3.Session()
            self._sqs_gateway = SqsGateway(session.client("sqs"))
        return self._sqs_gateway

    def on_mount(self: Self) -> None:
        self.push_screen(HomeScreen())
