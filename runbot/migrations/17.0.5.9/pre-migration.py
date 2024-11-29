def migrate(cr, version):
    cr.execute('ALTER TABLE runbot_build_config_step ADD COLUMN demo_mode VARCHAR DEFAULT \'default\';')
    cr.execute("""
        UPDATE runbot_build_config_step
           SET demo_mode='without_demo'
         WHERE extra_params LIKE '%--without-demo%';
    """)
