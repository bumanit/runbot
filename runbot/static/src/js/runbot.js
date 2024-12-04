(function($) {
    "use strict";   
    $(function () {
        $(document).on('click', '[data-runbot]', function (e) {
            e.preventDefault();
            var data = $(this).data();
            var operation = data.runbot;
            if (!operation) { 
                return; 
            }
            var xhr = new XMLHttpRequest();
            var url = e.target.href
            if (data.runbotBuild) {
                url = '/runbot/build/' + data.runbotBuild + '/' + operation
            }
            var elem = e.target 
            xhr.addEventListener('load', function () {
                if (operation == 'rebuild' && window.location.href.split('?')[0].endsWith('/build/' + data.runbotBuild)){
                    window.location.href = window.location.href.replace('/build/' + data.runbotBuild, '/build/' + xhr.responseText);
                } else if (operation == 'action') {
                    elem.parentElement.innerText = this.responseText
                } else {
                    window.location.reload();
                }
            });
            xhr.open('POST', url);
            xhr.send();
        });
    });
})(jQuery);


function copyToClipboard(text) {
    if (!navigator.clipboard) {
        console.error('Clipboard not supported');
        return;
    }
    navigator.clipboard.writeText(text);
}

const copyHashToClipboard = (hash) => {
    if (!navigator.clipboard) {
        return
    }
    navigator.clipboard.writeText(location.origin + location.pathname + `#${hash}`);
}

const switchTheme = (theme) => {
    document.documentElement.dataset.bsTheme = theme;
}

// setInterval(() => {
//     if (document.documentElement.dataset.bsTheme === 'dark') {
//         switchTheme('light');
//     } else {
//         switchTheme('dark');
//     }
// }, 2000)

const dark = switchTheme.bind(null, 'dark');
const legacy = switchTheme.bind(null, 'legacy');
const light = switchTheme.bind(null, 'light');
const red404 = switchTheme.bind(null, 'red404');

setTimeout(() => {
    const navbarElem = document.querySelector('nav.navbar');
    const toolbarElem = document.querySelector('.o_runbot_toolbar.position-sticky');

    if (navbarElem && toolbarElem) {
        toolbarElem.style.top = navbarElem.getBoundingClientRect().height;
        new ResizeObserver(() => {
            console.log('resize')
            toolbarElem.style.top = navbarElem.getBoundingClientRect().height;
        }).observe(navbarElem);
    }
}, 150);
