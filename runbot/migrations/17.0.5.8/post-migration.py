import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):

    #cr.execute('CREATE SEQUENCE runbot_build_error_id_seq')
    # update sequence
    cr.execute("SELECT setval('runbot_build_error_id_seq',  (SELECT MAX(id) FROM runbot_build_error))")
    cr.execute("ALTER TABLE runbot_build_error ALTER COLUMN id SET DEFAULT nextval('runbot_build_error_id_seq')")

    # get seen infos
    cr.execute("SELECT error_content_id, min(build_id), min(log_date), max(build_id), max(log_date), count(DISTINCT build_id) FROM runbot_build_error_link GROUP BY error_content_id")
    vals_by_error = {error: vals for error, *vals in cr.fetchall()}

    # first_seen_build_id was not stored, lets fill it and update all values for good mesure
    for error, vals in vals_by_error.items():
        cr.execute('UPDATE runbot_build_error_content SET first_seen_build_id = %s, first_seen_date = %s, last_seen_build_id = %s, last_seen_date = %s WHERE id=%s', (vals[0], vals[1], vals[2], vals[3], error))

    # generate flattened error hierarchy
    cr.execute('''SELECT
                    id,
                    parent_id
                FROM runbot_build_error_content
                ORDER BY id
                ''')

    error_by_parent = {}
    for error_id, parent_id in cr.fetchall():
        if parent_id:
            error_by_parent.setdefault(parent_id, []).append(error_id)
    stable = False
    while not stable:
        stable = True
        for parent, child_ids in error_by_parent.items():
            for child_id in child_ids:
                if parent == child_id:
                    continue
                sub_childrens = error_by_parent.get(child_id)
                if sub_childrens:
                    error_by_parent[parent] = error_by_parent[parent] + sub_childrens
                    error_by_parent[child_id] = []
                    stable = False
    for parent, child_ids in error_by_parent.items():
        if parent in child_ids:
            _logger.info('Breaking cycle parent on %s', parent)
            error_by_parent[parent] = [c for c in child_ids if c != parent]
            cr.execute('UPDATE runbot_build_error_content SET parent_id = null WHERE id=%s', (parent,))
    error_by_parent = {parent: chilren for parent, chilren in error_by_parent.items() if chilren}

    cr.execute('''SELECT
                    id,
                    active,
                    parent_id
                    random,
                    content,
                    test_tags,
                    tags_min_version_id,
                    tags_max_version_id,
                    team_id,
                    responsible,
                    customer,
                    fixing_commit,
                    fixing_pr_id
                FROM runbot_build_error_content
                WHERE parent_id IS null
                ORDER BY id
                ''')
    errors = cr.fetchall()
    nb_groups = len(error_by_parent)
    _logger.info('Creating %s errors', nb_groups)
    for error in errors:
        error_id, *values = error
        children = error_by_parent.get(error_id, [])
        assert not error_id in children
        all_errors = [error_id, *children]
        error_count = len(all_errors)

        # vals_by_error order: min(build_id), min(log_date), max(build_id), max(log_date)
        build_count = 0
        first_seen_build_id = first_seen_date = last_seen_build_id = last_seen_date = None
        if error_id in vals_by_error:
            error_vals = [vals_by_error[error_id] for error_id in all_errors if error_id in vals_by_error]
            first_seen_build_id = min(vals[0] for vals in error_vals)
            first_seen_date = min(vals[1] for vals in error_vals)
            last_seen_build_id = max(vals[2] for vals in error_vals)
            last_seen_date = max(vals[3] for vals in error_vals)
            build_count = sum(vals[4] for vals in error_vals)  # not correct for distinct but close enough
            assert first_seen_date <= last_seen_date
            assert first_seen_build_id <= last_seen_build_id
        name = values[2].split('\n')[0]

        values = [error_id, *values, last_seen_build_id, first_seen_build_id, last_seen_date, first_seen_date, build_count, error_count, name]

        cr.execute('''
        INSERT INTO runbot_build_error (
            id,
            active,
            random,
            description,
            test_tags,
            tags_min_version_id,
            tags_max_version_id,
            team_id,
            responsible,
            customer,
            fixing_commit,
            fixing_pr_id,
            last_seen_build_id,
            first_seen_build_id,
            last_seen_date,
            first_seen_date,
            build_count,
            error_count,
            name
        )
        VALUES (%s)
        RETURNING id
        ''' % ', '.join(['%s'] * len(values)), values)  # noqa: S608

        error_id = cr.fetchone()
        cr.execute('UPDATE runbot_build_error_content SET error_id = %s WHERE id in %s', (error_id, tuple(all_errors)))

    cr.execute('ALTER TABLE runbot_build_error_content ALTER COLUMN error_id SET NOT NULL')
    cr.execute('SELECT max(id) from runbot_build_error')
    cr.execute("SELECT SETVAL('runbot_build_error_id_seq', %s)", (cr.fetchone()[0] + 1,))
    _logger.info('Done')
