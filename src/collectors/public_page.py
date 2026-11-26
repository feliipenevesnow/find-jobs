"""Leitura limitada de páginas descobertas em índices públicos."""
import asyncio
import ipaddress
import socket
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup


async def public_address(host: str) -> str:
    records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(record[4][0]) for record in records]
    if not addresses or any(not address.is_global or address.is_multicast for address in addresses):
        raise ValueError("O destino não é um site público")
    return str(min(addresses, key=lambda address: address.version))


async def public_page(client: httpx.AsyncClient, url: str) -> tuple[str, BeautifulSoup]:
    async with asyncio.timeout(18):
        for _ in range(5):
            target = httpx.URL(url)
            if (target.scheme != "https" or not target.host or target.userinfo or target.port not in (None, 443)
                    or "\\" in url or "%" in target.host):
                raise ValueError("Endereço de página inválido")
            # Fixa o IP validado inclusive nos redirecionamentos, preservando Host e TLS do site.
            ip = await public_address(target.host)
            headers = {
                "Host": target.raw_host.decode(),
                "Connection": "close",
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            }
            async with client.stream("GET", target.copy_with(host=ip), headers=headers, follow_redirects=False,
                                     extensions={"sni_hostname": target.raw_host.decode()}) as response:
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(str(target), response.headers["location"])
                    continue
                response.raise_for_status()
                if "html" not in response.headers.get("content-type", "").lower():
                    raise ValueError("O resultado não é uma página HTML")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise ValueError("Página acima do limite de leitura")
                return str(target), BeautifulSoup(bytes(data), "html.parser", from_encoding=response.charset_encoding)
    raise ValueError("Excesso de redirecionamentos")
