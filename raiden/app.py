# -*- coding: utf8 -*-
from __future__ import print_function

import signal
import gevent
import click
from ethereum import slogging
from pyethapp.rpc_client import JSONRPCClient

from raiden.raiden_service import RaidenService
from raiden.network.discovery import ContractDiscovery
from raiden.network.transport import UDPTransport
from raiden.network.rpc.client import BlockChainService
from raiden.console import Console
from raiden.utils import pex, split_endpoint

from raiden.network import nat

log = slogging.get_logger(__name__)  # pylint: disable=invalid-name


INITIAL_PORT = 40001
DEFAULT_SETTLE_TIMEOUT = 50
DEFAULT_REVEAL_TIMEOUT = 3


class App(object):  # pylint: disable=too-few-public-methods
    default_config = dict(
        host='',
        port=INITIAL_PORT,
        privatekey_hex='',
        # number of blocks that a node requires to learn the secret before the lock expires
        reveal_timeout=DEFAULT_REVEAL_TIMEOUT,
        settle_timeout=DEFAULT_SETTLE_TIMEOUT,
        # how long to wait for a transfer until TimeoutTransfer is sent (time in milliseconds)
        msg_timeout=100.00,
    )

    def __init__(self, config, chain, discovery, transport_class=UDPTransport):
        self.config = config
        self.discovery = discovery
        self.transport = transport_class(config['host'], config['port'])
        self.raiden = RaidenService(
            chain,
            config['privatekey_hex'].decode('hex'),
            self.transport,
            discovery,
            config,
        )
        self.services = {'raiden': self.raiden}
        self.start_console = True

    def __repr__(self):
        return '<{} {}>'.format(
            self.__class__.__name__,
            pex(self.raiden.address),
        )

    def stop(self):
        self.transport.stop()
        self.raiden.stop()


@click.option(
    '--privatekey',
    help='Asks for the hex encoded ethereum private key.\n'
    'WARNING: do not give the privatekey on the commandline, instead wait for the prompt!',
    type=str,
    prompt=True,
    hide_input=True,
)
@click.option(
    '--eth_rpc_endpoint',
    help='"host:port" address of ethereum JSON-RPC server.',
    default='127.0.0.1:8545',  # geth default jsonrpc port
    type=str,
)
@click.option(
    '--registry_contract_address',
    help='hex encoded address of the registry contract.',
    default='5d2365c483d31c3da34ef7bb5ea710528370c580',  # testnet default
    type=str,
)
@click.option(
    '--discovery_contract_address',
    help='hex encoded address of the discovery contract.',
    default='662d172c9170f8a97e13a7a0309c8302e20b1123',  # testnet default
    type=str,
)
@click.option(
    '--listen_address',
    help='"host:port" for the raiden service to listen on.',
    default="0.0.0.0:{}".format(INITIAL_PORT),
    type=str,
)
@click.option(  # FIXME: implement NAT-punching
    '--external_listen_address',
    help='external "host:port" where the raiden service can be contacted on (through NAT).',
    default='',
    type=str,
    )
@click.option(
    '--logging',
    help='ethereum.slogging config-string (\'<logger1>:<level>,<logger2>:<level>\')',
    default=':INFO',
    type=str,
    )
@click.command()
def app(privatekey, eth_rpc_endpoint, registry_contract_address,
        discovery_contract_address, listen_address, external_listen_address, logging):

    slogging.configure(logging)

    (listen_host, listen_port) = split_endpoint(listen_address)

    if not external_listen_address:
        # notify('if you are behind a NAT, you should set
        # `external_listen_address` and configure port forwarding on your router')
        host, port = nat.open_port(listen_port) or (None, None)
        if host:
            nat_port = True
            external_listen_address = "{}:{}".format(host, port)
        else:
            external_listen_address = listen_address

    # config_file = args.config_file
    rpc_connection = split_endpoint(eth_rpc_endpoint)

    config = App.default_config.copy()
    config['host'] = listen_host
    config['port'] = listen_port
    config['privatekey_hex'] = privatekey

    jsonrpc_client = JSONRPCClient(
        privkey=privatekey,
        host=rpc_connection[0],
        port=rpc_connection[1],
        print_communication=False,
    )

    blockchain_service = BlockChainService(
        jsonrpc_client,
        registry_contract_address.decode('hex'),
    )
    discovery = ContractDiscovery(jsonrpc_client, discovery_contract_address.decode('hex'))  # FIXME: double encoding

    app = App(config, blockchain_service, discovery)

    discovery.register(app.raiden.address, *split_endpoint(external_listen_address))

    app.raiden.register_registry(blockchain_service.default_registry)

    # TODO:
    # - Ask for confirmation to quit if there are any locked transfers that did
    # not timeout.

    console = Console(app)
    console.start()

    # wait for interrupt
    event = gevent.event.Event()
    gevent.signal(signal.SIGQUIT, event.set)
    gevent.signal(signal.SIGTERM, event.set)
    gevent.signal(signal.SIGINT, event.set)
    event.wait()

    if nat_port:
        nat.release_port(port)

    app.stop()


if __name__ == '__main__':
    app()
