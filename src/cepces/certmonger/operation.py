# -*- coding: utf-8 -*-
#
# This file is part of cepces.
#
# cepces is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# cepces is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with cepces.  If not, see <http://www.gnu.org/licenses/>.
#
# pylint: disable=too-few-public-methods
"""This module contains all the supported certmonger operations."""

from abc import ABCMeta, abstractmethod
import os
import sys
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cepces import __title__, __version__
from cepces import Base
from cepces.core import PartialChainError
from cepces.certmonger.core import MissingEnvironmentVariable
from cepces.certmonger.core import Result as CertmongerResult
from cepces.soap.service import SOAPFault


class Operation(Base, metaclass=ABCMeta):
    """Abstract base class used by child classes mapping certmonger operations.

    As certmonger calls the helper, all of the necessary data is available as
    environment variables. Some of these variables may be required to perform
    an action, whereas others are optional. This base class helps ensure
    everything required is present, or fails otherwise.

    Two class variables are used to define this behaviour:

    * `required`: A list containing all required environment variables.
    * `optional`: A list containing tuples of all optional environment
                    variables and their defaults (e.g., '("VAR", None)').

    An extra class variable, `name`, is used to distinguish the mapped
    certmonger operation.
    """

    name = None
    required = []
    optional = []

    def __init__(self, service, out=sys.stdout, logger=None):
        """Initializes an Operation.

        All required and optional environment variables are verified, read and
        stored in the instance as a dictionary.

        :param service: the cepces service
        :param out: default output stream (default: sys.stdout)
        :raise MissingEnvironmentVariable: if a required environment variable
                                           is not present.
        """
        super().__init__(logger=logger)

        self._service = service
        self._out = out
        self._vars = {}

        # Verify that all required environment variables are present.
        for var in self.__class__.required:
            if var not in os.environ:
                raise MissingEnvironmentVariable(var)
            else:
                self._vars[var] = os.environ[var]

        # Get all optional variables and set their defaults if they're missing.
        for var, default in self.__class__.optional:
            if var not in os.environ:
                self._vars[var] = default
            else:
                self._vars[var] = os.environ[var]

    @abstractmethod
    def __call__(self):
        """Calls the operation to let it performs its logic.

        :return: the certmonger result code.
        """


class Submit(Operation):
    """Attempt to enroll a new certificate."""

    name = "SUBMIT"
    required = ["CERTMONGER_CSR"]
    optional = [
        ("CERTMONGER_CERTIFICATE", None),
    ]

    def __call__(self):
        service = self._service

        pem = self._vars["CERTMONGER_CSR"].strip()
        csr = x509.load_pem_x509_csr(pem.encode(), default_backend())

        self._logger.debug("Sending CSR: %s", pem)

        try:
            result = service.request(
                csr,
                renew=self._vars["CERTMONGER_CERTIFICATE"] is not None,
            )
        except SOAPFault as error:
            print(error, file=self._out)

            return CertmongerResult.REJECTED

        self._logger.debug("Result is: %s", result)

        # If we have a certificate, return it. Otherwise, ask certmonger to
        # wait a bit.
        if result.token:
            self._logger.debug("Token is: %s", result.token)
            pem = result.token.public_bytes(serialization.Encoding.PEM)

            print(pem.decode().strip(), file=self._out)

            return CertmongerResult.ISSUED

        # Output a "cookie" that can be used to later poll the status.
        print(
            "{}\n{},{}".format(
                service._config.poll_interval,
                result.request_id,
                result.reference,
            ),
            file=self._out,
        )

        return CertmongerResult.WAITMORE


class Poll(Operation):
    """Poll the status for a previous deferred request."""

    name = "POLL"
    required = ["CERTMONGER_CA_COOKIE"]

    def __call__(self):
        service = self._service

        cookie = self._vars["CERTMONGER_CA_COOKIE"]
        request_id, reference = cookie.split(",", maxsplit=1)

        try:
            result = service.poll(int(request_id), reference)
        except SOAPFault as error:
            print(error, file=self._out)

            return CertmongerResult.REJECTED

        # If we have a certificate, return it. Otherwise, ask certmonger to
        # wait a bit.
        if result.token:
            self._logger.debug("Token is: %s", result.token)
            pem = result.token.public_bytes(serialization.Encoding.PEM)

            print(pem.decode().strip(), file=self._out)

            return CertmongerResult.ISSUED

        # Output a "cookie" that can be used to later poll the status.
        print(
            "{}\n{},{}".format(
                service._config.poll_interval,
                result.request_id,
                result.reference,
            ),
            file=self._out,
        )

        return CertmongerResult.WAITMORE


class Identify(Operation):
    """Outputs version information for this helper."""

    name = "IDENTIFY"

    def __call__(self):
        print("{} {}".format(__title__, __version__), file=self._out)

        return CertmongerResult.DEFAULT


class GetNewRequestRequirements(Operation):
    """Outputs a list of required environment variables for submission."""

    name = "GET-NEW-REQUEST-REQUIREMENTS"

    def __call__(self):
        # Output a list of required environment variables.
        print("CERTMONGER_CA_PROFILE", file=self._out)

        return CertmongerResult.DEFAULT


class GetRenewRequestRequirements(Operation):
    """Outputs a list of required environment variables for renewal."""

    name = "GET-RENEW-REQUEST-REQUIREMENTS"

    def __call__(self):
        # Output a list of required environment variables.
        print("CERTMONGER_CA_PROFILE", file=self._out)

        return CertmongerResult.DEFAULT


class GetSupportedTemplates(Operation):
    """Outputs a list of supported templates."""

    name = "GET-SUPPORTED-TEMPLATES"

    def __call__(self):
        templates = self._service.templates

        if templates:
            for template in templates:
                print(template, file=self._out)

        return CertmongerResult.DEFAULT


class GetDefaultTemplate(Operation):
    """Outputs the default template (which is nothing).

    MS-XCEP doesn't specify a default template/policy, so this operation always
    results in no output.
    """

    name = "GET-DEFAULT-TEMPLATE"

    def __call__(self):
        return CertmongerResult.DEFAULT


class FetchRoots(Operation):
    """Outputs suggested nick-names and certificates for all CAs."""

    name = "FETCH-ROOTS"

    def __call__(self):
        oid_cn = x509.oid.NameOID.COMMON_NAME

        # Retrieve the certificate chain as far as possible.
        try:
            certs = list(self._service.certificate_chain or [])
        except PartialChainError as error:
            certs = error.result

        output = []

        for cert in certs:
            names = cert.subject.get_attributes_for_oid(oid_cn)
            pem = cert.public_bytes(serialization.Encoding.PEM)

            output.append(
                "{}\n{}".format(
                    names[0].value,
                    pem.decode().strip(),
                ),
            )

        print("\n".join(output), file=self._out)

        return CertmongerResult.DEFAULT
