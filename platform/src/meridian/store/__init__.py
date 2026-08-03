"""SQL access layer.

Hand-written SQL over ``psycopg``, returning frozen dataclasses. There is no ORM:
the schema is defined once, in ``deploy/migrations``, and an ORM would add a second
definition that must be kept in sync with it by hand.

This layer *is* the abstraction docs/ARCHITECTURE.md rule 1 asks for — cross-module
access goes through interfaces rather than database queries, and those interfaces
are the module protocols, not this package. Modules call each other; only this
package calls the database.
"""
