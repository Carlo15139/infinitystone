# -*- coding: utf-8 -*-
# Copyright (c) 2018 Christiaan Frans Rademan.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of the copyright holders nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF
# THE POSSIBILITY OF SUCH DAMAGE.
from luxon import GetLogger
from luxon import register_resource
from luxon import register_resources
from luxon import g
from luxon.exceptions import ValidationError
from luxon.exceptions import AccessDenied
from luxon.exceptions import HTTPNotFound
from luxon import db
from luxon.utils.timezone import now
from infinitystone.utils.api import parse_sql_where

from uuid import uuid4
import json

from infinitystone.utils.auth import user_domains

log = GetLogger(__name__)


def check_unique(conn, id, role, domain, tenant_id):
    """Function to check if user role assignment is unique.

    Args:
        conn (obj): DB connection object.
        id (str): UUID of user.
        role (str): UUID of role.
        domain (str): Name of the domain.
        tenant_id (str): UUID of the tenant.
    """
    sql = "SELECT id FROM luxon_user_role WHERE user_id=? AND role_id=? AND "
    vals = [id, role]
    where = {"domain": domain, "tenant_id": tenant_id}
    query, addvals = parse_sql_where(where)
    sql += query
    cur = conn.execute(sql, (vals + addvals))
    if cur.fetchone():
        raise ValidationError("Entry for user '%s' role '%s' "
                              "already exists on domain '%s'"
                              " and tenant '%s'."
                              % (id, role, domain, tenant_id))


def check_context_auth(conn, user_id, domain, tenant_id):
    """Verify if users has jurisdiction over requested domain/tenant.

    The default Root user can assign any role to any user, if and only if
    the user exists in the supplied tenant and domain.

    Only users with Admin or Root roles are allowed to assign roles to
    users. This function will raise an error if
        * the requesting user is not an admin user on
          the requested domain/tenant.
        * the requested user does not exist in
          the requested domain/tenant.

    Args:
        conn (obj): DB connection object.
        user_id (str): UUID of user.
        domain (str): Name of the domain.
        tenant_id (str): UUID of the tenant.
    """
    # Checking if Requesting user is Admin/Root in domain and tenant
    req_user_id = g.current_request.token.user_id
    if req_user_id != '00000000-0000-0000-0000-000000000000':
        cur = conn.execute("SELECT id FROM luxon_role WHERE name=?",
                           ('Administrator',))
        admin_id = cur.fetchone()['id']

        where = {'user_id': req_user_id,
                 'domain': domain,
                 'tenant_id': tenant_id}
        query, vals = parse_sql_where(where)
        query += " AND ('role_id'='00000000-0000-0000-0000-000000000000'"
        query += " OR role_id=?)"
        vals.append(admin_id)

        sql = "SELECT id FROM luxon_user_role WHERE " + query
        cur = conn.execute(sql, vals)
        if not cur.fetchone():
            raise AccessDenied("User %s not authorized in requested context "
                               ": domain '%s', tenant_id '%s'"
                               % (req_user_id, domain, tenant_id))

    # Checking if Requested User belongs in domain/tenant
    where = {'id': user_id,
             'domain': domain,
             'tenant_id': tenant_id}
    query, vals = parse_sql_where(where)
    sql = "SELECT id FROM luxon_user WHERE " + query
    cur = conn.execute(sql, vals)
    if not cur.fetchone():
        raise AccessDenied("User %s does not exist in context - "
                           "domain: '%s', tenant_id: '%s'"
                           % (user_id, domain, tenant_id))


@register_resource('GET', '/v1/rbac/domains')
def rbac_domains(req, resp):
    search = req.query_params.get('term')
    domains_list = user_domains(req.token.user_id)
    if search is not None:
        filtered = []
        for domain in domains_list:
            if search in domain:
                filtered.append(domain)
        return filtered
    return domains_list


@register_resource('GET', '/v1/rbac/user/{id}')
def user_roles(req, resp, id):
    sql = "SELECT * FROM luxon_user_role WHERE " \
          "user_id=?"
    vals = [ id ]
    domain = req.get_header("X-Domain", default=None)
    if domain:
        sql += " AND domain=?"
        vals.append(domain)
    tenant_id = req.get_header('X-Tenant-Id', default=None)
    if tenant_id:
        sql += " AND tenant_id=?"
        vals.append(tenant_id)
    with db() as conn:
        cur = conn.execute(sql, vals)
        return json.dumps(cur.fetchall())


@register_resources()
class AddUserRoles():
    def __init__(self):
        g.router.add('POST', '/v1/rbac/user/{id}/{role}',
                     self.add_user_role, tag="admin")
        g.router.add('POST', '/v1/rbac/user/{id}/{role}/{domain}',
                     self.add_user_role, tag="admin")
        g.router.add('POST', '/v1/rbac/user/{id}/{role}/{domain}/{tenant_id}',
                     self.add_user_role, tag="admin")

    def add_user_role(self, req, resp, id, role, domain=None, tenant_id=None):
        """
        Associate role to a user.

        Args:
            id (str): UUID of user.
            role (str): UUID of role.
            domain (str): Name of domain (defaults to None).
                          Use the text "none" to indicate global domain
                          when tenant_id is supplied.
            tenant_id (str): UUID of tenant (defaults to None).

        Example return data:

        .. code-block:: json

            {
                "id": "e729af96-5672-4669-b4a1-6251493a67fa",
                "user_id": "e95ec7b1-4f0f-4c70-991f-4bb1bec6a524",
                "role_id": "08034650-1438-4e56-b5a8-674ede74fe83",
                "domain": "default",
                "tenant_id": null
            }
        """
        if domain is not None and domain.lower() == "none":
            domain = None
        with db() as conn:
            check_context_auth(conn, id, domain, tenant_id)
            # Even though we have unique constraint, sqlite
            # does not consider null as unique. ref:
            # https://goo.gl/JmjT5G
            # So need to manually check that.
            check_unique(conn, id, role, domain, tenant_id)

            sql = "INSERT INTO luxon_user_role " \
                  "(`id`,`role_id`,`tenant_id`,`user_id`," \
                  "`domain`,`creation_time`) " \
                  "VALUES (?,?,?,?,?,?)"
            user_role_id = str(uuid4())
            conn.execute(sql, (user_role_id, role, tenant_id,
                               id, domain, now()))
            conn.commit()
            user_role = {"id": user_role_id,
                         "user_id": id,
                         "role_id": role,
                         "domain": domain,
                         "tenant_id": tenant_id}
            return json.dumps(user_role, indent=4)


@register_resources()
class RmUserRoles():
    def __init__(self):
        g.router.add('DELETE', '/v1/rbac/user/{id}/{role}',
                     self.rm_user_role, tag="admin")
        g.router.add('DELETE', '/v1/rbac/user/{id}/{role}/{domain}',
                     self.rm_user_role, tag="admin")
        g.router.add('DELETE',
                     '/v1/rbac/user/{id}/{role}/{domain}/{tenant_id}',
                     self.rm_user_role, tag="admin")

    def rm_user_role(self, req, resp, id, role, domain=None, tenant_id=None):
        """
        Remove a role associated to a user.

        Args:
            id (str): UUID of user.
            role (str): UUID of role.
            domain (str): Name of domain (defaults to None).
                          Use the text "none" to indicate global domain
                          when tenant_id is supplied.
            tenant_id (str): UUID of tenant (defaults to None).

        Returns:
            200 OK with blank body if successful
            404 Not Found if now entry was affected

        """
        with db() as conn:
            where = {'user_id': id,
                     'role_id': role,
                     'tenant_id': tenant_id,
                     'domain': domain}
            query, vals = parse_sql_where(where)
            sql = "DELETE FROM luxon_user_role WHERE " + query
            cur = conn.execute(sql, vals)
            conn.commit()
            if not cur.rowcount:
                raise HTTPNotFound("No entry for %s" % where)
                # Not Returning any body - 200 OK says it all.
