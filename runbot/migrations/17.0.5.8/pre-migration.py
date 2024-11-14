def migrate(cr, version):
    cr.execute('ALTER TABLE runbot_build_error RENAME TO runbot_build_error_content')
    cr.execute('CREATE SEQUENCE runbot_build_error_content_id_seq')
    cr.execute("SELECT setval('runbot_build_error_content_id_seq',  (SELECT MAX(id) FROM runbot_build_error_content))")
    cr.execute("ALTER TABLE runbot_build_error_content ALTER COLUMN id SET DEFAULT nextval('runbot_build_error_content_id_seq')")
    cr.execute('ALTER TABLE runbot_build_error_content ADD COLUMN first_seen_build_id INT')
    cr.execute('ALTER TABLE runbot_build_error_link RENAME COLUMN build_error_id TO error_content_id')
