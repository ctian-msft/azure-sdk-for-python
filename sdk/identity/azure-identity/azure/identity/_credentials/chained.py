# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
import logging
from typing import Any, Optional, TYPE_CHECKING
from azure.core.exceptions import ClientAuthenticationError

from azure.core.credentials import AccessToken
from .. import CredentialUnavailableError
from .._internal import within_credential_chain

if TYPE_CHECKING:
    from azure.core.credentials import TokenCredential

_LOGGER = logging.getLogger(__name__)


def _get_error_message(history):
    attempts = []
    for credential, error in history:
        if error:
            attempts.append("{}: {}".format(credential.__class__.__name__, error))
        else:
            attempts.append(credential.__class__.__name__)
    return """
Attempted credentials:\n\t{}""".format(
        "\n\t".join(attempts)
    )


class ChainedTokenCredential:
    """A sequence of credentials that is itself a credential.

    Its :func:`get_token` method calls ``get_token`` on each credential in the sequence, in order, returning the first
    valid token received.

    :param credentials: credential instances to form the chain
    :type credentials: ~azure.core.credentials.TokenCredential

    .. admonition:: Example:

        .. literalinclude:: ../samples/credential_creation_code_snippets.py
            :start-after: [START create_chained_token_credential]
            :end-before: [END create_chained_token_credential]
            :language: python
            :dedent: 4
            :caption: Create a ChainedTokenCredential.
    """

    def __init__(self, *credentials):
        # type: (*TokenCredential) -> None
        if not credentials:
            raise ValueError("at least one credential is required")

        self._successful_credential = None  # type: Optional[TokenCredential]
        self.credentials = credentials

    def __enter__(self):
        for credential in self.credentials:
            credential.__enter__()  # type: ignore
        return self

    def __exit__(self, *args: Any):
        for credential in self.credentials:
            credential.__exit__(*args)  # type: ignore

    def close(self) -> None:
        """Close the transport session of each credential in the chain."""
        self.__exit__()

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:  # pylint:disable=unused-argument
        """Request a token from each chained credential, in order, returning the first token received.

        This method is called automatically by Azure SDK clients.

        :param str scopes: desired scopes for the access token. This method requires at least one scope.
            For more information about scopes, see
            https://learn.microsoft.com/azure/active-directory/develop/scopes-oidc.

        :return: An access token with the desired scopes.
        :rtype: ~azure.core.credentials.AccessToken
        :raises ~azure.core.exceptions.ClientAuthenticationError: no credential in the chain provided a token
        """
        within_credential_chain.set(True)
        history = []
        for credential in self.credentials:
            try:
                token = credential.get_token(*scopes, **kwargs)
                _LOGGER.info("%s acquired a token from %s", self.__class__.__name__, credential.__class__.__name__)
                self._successful_credential = credential
                return token
            except CredentialUnavailableError as ex:
                # credential didn't attempt authentication because it lacks required data or state -> continue
                history.append((credential, ex.message))
            except Exception as ex:  # pylint: disable=broad-except
                # credential failed to authenticate, or something unexpectedly raised -> break
                history.append((credential, str(ex)))
                _LOGGER.debug(
                    '%s.get_token failed: %s raised unexpected error "%s"',
                    self.__class__.__name__,
                    credential.__class__.__name__,
                    ex,
                    exc_info=True,
                )
                break

        within_credential_chain.set(False)
        attempts = _get_error_message(history)
        message = (
            self.__class__.__name__
            + " failed to retrieve a token from the included credentials."
            + attempts
            + "\nTo mitigate this issue, please refer to the troubleshooting guidelines here at "
            "https://aka.ms/azsdk/python/identity/defaultazurecredential/troubleshoot."
        )
        _LOGGER.warning(message)
        raise ClientAuthenticationError(message=message)
