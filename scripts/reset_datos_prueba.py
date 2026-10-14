"""Reinicio explícito de datos de prueba de AgendaAI.

Simula por defecto. En --apply ejecuta DELETE (no TRUNCATE/CASCADE)
transaccionalmente, respetando las claves foráneas. Conserva RAG
corporativo, recursos y reglas de disponibilidad.
"""
import argparse
import os
from pathlib import Path
from collections import defaultdict

import psycopg
from psycopg import sql
from dotenv import load_dotenv

CANDIDATES = (
    'email_notifications', 'receipts', 'calendar_sync_jobs',
    'pending_bookings', 'user_conversation_state',
    'verified_email_contacts', 'messages', 'agent_memories',
    'bookings', 'bot_users',
)
PRESERVE = {'company_knowledge_chunks', 'booking_resources', 'availability_rules'}


def dependencies(conn):
    """Tuplas (nombre_restriccion, tabla_hija, tabla_padre) en public."""
    return conn.execute("""
        SELECT con.conname, src.relname, dest.relname
        FROM pg_constraint con
        JOIN pg_class src ON src.oid = con.conrelid
        JOIN pg_namespace sn ON sn.oid = src.relnamespace
        JOIN pg_class dest ON dest.oid = con.confrelid
        JOIN pg_namespace dn ON dn.oid = dest.relnamespace
        WHERE con.contype = 'f'
          AND sn.nspname = 'public'
          AND dn.nspname = 'public'
    """).fetchall()


def deletion_order(tables, foreign_keys):
    """Hijas primero; aborta si se detecta un ciclo que necesite otro tratamiento."""
    target = set(tables)
    edges = defaultdict(set)
    indegree = {table: 0 for table in tables}
    for _, child, parent in foreign_keys:
        if child in target and parent in target and child != parent:
            if parent not in edges[child]:
                edges[child].add(parent)
                indegree[parent] += 1
    pending = [table for table in tables if indegree[table] == 0]
    result = []
    while pending:
        child = pending.pop(0)
        result.append(child)
        for parent in sorted(edges[child]):
            indegree[parent] -= 1
            if indegree[parent] == 0:
                pending.append(parent)
    if len(result) != len(tables):
        raise RuntimeError('Se detectó un ciclo de claves foráneas. Revisa el esquema manualmente.')
    return result


def main():
    parser = argparse.ArgumentParser(description='Reset seguro de datos de prueba de AgendaAI')
    parser.add_argument('--apply', action='store_true', help='Borrar tras confirmación explícita')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    url = os.getenv('DATABASE_URL')
    if not url:
        parser.error('Falta DATABASE_URL en .env')

    with psycopg.connect(url, connect_timeout=15) as conn:
        # Solo se hacen SELECT hasta recibir confirmación en --apply.
        with conn.transaction():
            existing = {r[0] for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()}
            tables = [t for t in CANDIDATES if t in existing]
            foreign_keys = dependencies(conn)
            missing = [(name, child, parent) for name, child, parent in foreign_keys
                       if parent in tables and child not in tables]
            print('TABLAS QUE SE CONSERVAN:', ', '.join(sorted(PRESERVE)))
            print('TABLAS PREVISTAS PARA BORRAR:')
            total = 0
            for table in tables:
                count = conn.execute(
                    sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(table))
                ).fetchone()[0]
                total += count
                print(f'  {table}: {count} filas')
            if missing:
                print('DEPENDENCIAS AJENAS (revisar manualmente):', missing)
                raise RuntimeError('Borrado bloqueado: hay tablas que dependen de las seleccionadas')
            order = deletion_order(tables, foreign_keys)
            print('ORDEN SEGURO DE BORRADO:', ' -> '.join(order))
            print('TOTAL DE FILAS A BORRAR:', total)
            if not args.apply:
                print('SIMULACIÓN: no se borró nada. Usa --apply cuando tengas respaldo.')
                return  # No hay escrituras: el contexto cierra la transacción sin cambios.

        print('ATENCIÓN: los datos seleccionados se borrarán de forma permanente.')
        if input('Escribe exactamente BORRAR PRUEBAS para continuar: ').strip() != 'BORRAR PRUEBAS':
            print('Operación cancelada. No se borró nada.')
            return

        # Segunda comprobación y DELETE en una única transacción:
        # ante cualquier error, psycopg revierte todas las eliminaciones.
        with conn.transaction():
            current = dependencies(conn)
            missing_now = [(name, child, parent) for name, child, parent in current
                           if parent in tables and child not in tables]
            if missing_now:
                raise RuntimeError(f'El esquema cambió: dependencias nuevas {missing_now}')
            order_now = deletion_order(tables, current)
            for table in order_now:
                conn.execute(sql.SQL('DELETE FROM public.{}').format(sql.Identifier(table)))
        print('Borrado completado. Se conservaron los conocimientos RAG y las reglas de atención.')
        print('Nota: revisa por separado la limpieza del calendario de Google.')


if __name__ == '__main__':
    main()
