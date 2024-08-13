/** @odoo-module */

import {useEffect} from "@odoo/owl";
import {x2ManyCommands} from "@web/core/orm_service";
import {registry} from "@web/core/registry";
import {FormController} from "@web/views/form/form_controller";
import {formView} from "@web/views/form/form_view";

class FreezeController extends FormController {
    setup() {
        super.setup();

        useEffect(() => {
            const interval = setInterval(async () => {
                const root = this.model.root;
                if (await root.isDirty()) {
                    root.update({
                        required_pr_ids: x2ManyCommands.set(
                            root.data.required_pr_ids.currentIds,
                        ),
                    });
                } else {
                    root.load();
                }
            }, 1000);
            return () => {
                clearInterval(interval);
            };
        }, () => []);
    }
}

registry.category("views").add('freeze_wizard', {
    ...formView,
    Controller: FreezeController,
});
