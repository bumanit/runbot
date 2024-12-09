import publicWidget from "@web/legacy/js/public/public_widget";


publicWidget.registry.RunbotPage = publicWidget.Widget.extend({
    // This selector should not be so broad.
    selector: 'body',
    events: {
        'click [data-runbot]': '_onClickDataRunbot',
        'click [data-runbot-clipboard]': '_onClickRunbotCopy',
    },

    _onClickDataRunbot: async (event) => {
        const { currentTarget: target } = event;
        if (!target) {
            return;
        }
        event.preventDefault();
        const { runbot: operation, runbotBuild } = target.dataset;
        if (!operation) {
            return;
        }
        let url = target.href;
        if (runbotBuild) {
            url = `/runbot/build/${runbotBuild}/${operation}`
        }
        const response = await fetch(url, {
            method: 'POST',
        });
        if (operation == 'rebuild' && window.location.href.split('?')[0].endsWith(`/build/${runbotBuild}`)) {
            window.location.href = window.location.href.replace('/build/' + runbotBuild, '/build/' + await response.text());
        } else if (operation == 'action') {
            target.parentElement.innerText = await response.text();
        } else {
            window.location.reload();
        }
    },

    _onClickRunbotCopy: ({ currentTarget: target }) => {
        if (!navigator.clipboard || !target) {
            return;
        }
        navigator.clipboard.writeText(
            target.dataset.runbotClipboard
        );
    }
});
