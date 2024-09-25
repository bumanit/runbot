def migrate(cr, version):
    cr.execute('ALTER TABLE runbot_build_error RENAME TO runbot_build_error_content')
    cr.execute('ALTER TABLE runbot_build_error_content ADD COLUMN first_seen_build_id INT')
    cr.execute('ALTER TABLE runbot_build_error_link RENAME COLUMN build_error_id TO error_content_id')
