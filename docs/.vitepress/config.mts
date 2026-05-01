import { defineConfig } from 'vitepress'
import { withMermaid } from 'vitepress-plugin-mermaid'

export default withMermaid(
  defineConfig({
    title: 'Quicksand',
    description: 'Full Linux VMs from Python',
    base: '/quicksand/',

    themeConfig: {
      nav: [
        {
          text: 'User Guide',
          items: [
            { text: 'Overview', link: '/user-guide/' },
            { text: 'Installation', link: '/user-guide/01-installation' },
            { text: 'Sandbox Lifecycle', link: '/user-guide/02-sandbox-lifecycle' },
            { text: 'Running Commands', link: '/user-guide/03-running-commands' },
            { text: 'File Exchange', link: '/user-guide/04-file-exchange' },
            { text: 'Save and Rollback', link: '/user-guide/05-save-and-rollback' },
            { text: 'Desktop Control', link: '/user-guide/06-desktop-control' },
            { text: 'Network and Isolation', link: '/user-guide/07-network-and-isolation' },
            { text: 'Performance', link: '/user-guide/08-performance' },
          ],
        },
        {
          text: 'Under the Hood',
          items: [
            { text: 'Overview', link: '/under-the-hood/' },
            { text: 'Installation', link: '/under-the-hood/01-installation' },
            { text: 'Sandbox Lifecycle', link: '/under-the-hood/02-sandbox-lifecycle' },
            { text: 'Running Commands', link: '/under-the-hood/03-running-commands' },
            { text: 'File Exchange', link: '/under-the-hood/04-file-exchange' },
            { text: 'Save and Rollback', link: '/under-the-hood/05-save-and-rollback' },
            { text: 'Desktop Control', link: '/under-the-hood/06-desktop-control' },
            { text: 'Network and Isolation', link: '/under-the-hood/07-network-and-isolation' },
            { text: 'Performance', link: '/under-the-hood/08-performance' },
          ],
        },
        {
          text: 'Contributing',
          items: [
            { text: 'Overview', link: '/contributor-guide/' },
            { text: 'Creating Images', link: '/contributor-guide/01-creating-images' },
            { text: 'Extending the Sandbox', link: '/contributor-guide/02-extending-the-sandbox' },
            { text: 'Testing', link: '/contributor-guide/03-testing' },
            { text: 'Releasing', link: '/contributor-guide/04-releasing' },
          ],
        },
        {
          text: 'Packages',
          items: [
            { text: 'Overview', link: '/packages/' },
            { text: 'quicksand', link: '/packages/quicksand' },
            { text: 'quicksand-core', link: '/packages/quicksand-core' },
            { text: 'quicksand-qemu', link: '/packages/quicksand-qemu' },
            { text: 'quicksand-smb', link: '/packages/quicksand-smb' },
            { text: 'quicksand-ubuntu', link: '/packages/quicksand-ubuntu' },
            { text: 'quicksand-alpine', link: '/packages/quicksand-alpine' },
            { text: 'quicksand-ubuntu-desktop', link: '/packages/quicksand-ubuntu-desktop' },
            { text: 'quicksand-alpine-desktop', link: '/packages/quicksand-alpine-desktop' },
            { text: 'quicksand-agent', link: '/packages/quicksand-agent' },
            { text: 'quicksand-cua', link: '/packages/quicksand-cua' },
            { text: 'quicksand-image-tools', link: '/packages/quicksand-image-tools' },
            { text: 'quicksand-base-scaffold', link: '/packages/quicksand-base-scaffold' },
            { text: 'quicksand-overlay-scaffold', link: '/packages/quicksand-overlay-scaffold' },
            { text: 'quicksand-build-tools', link: '/packages/quicksand-build-tools' },
            { text: 'quicksand-gh-runners', link: '/packages/quicksand-gh-runners' },
          ],
        },
        { text: 'Changelog', link: '/reference/changelog' },
      ],

      sidebar: {
        '/user-guide/': [
          {
            text: 'User Guide',
            items: [
              { text: 'Overview', link: '/user-guide/' },
              { text: 'Installation', link: '/user-guide/01-installation' },
              { text: 'Sandbox Lifecycle', link: '/user-guide/02-sandbox-lifecycle' },
              { text: 'Running Commands', link: '/user-guide/03-running-commands' },
              { text: 'File Exchange', link: '/user-guide/04-file-exchange' },
              { text: 'Save and Rollback', link: '/user-guide/05-save-and-rollback' },
              { text: 'Desktop Control', link: '/user-guide/06-desktop-control' },
              { text: 'Network and Isolation', link: '/user-guide/07-network-and-isolation' },
              { text: 'Performance', link: '/user-guide/08-performance' },
            ],
          },
        ],
        '/under-the-hood/': [
          {
            text: 'Under the Hood',
            items: [
              { text: 'Overview', link: '/under-the-hood/' },
              { text: 'Installation', link: '/under-the-hood/01-installation' },
              { text: 'Sandbox Lifecycle', link: '/under-the-hood/02-sandbox-lifecycle' },
              { text: 'Running Commands', link: '/under-the-hood/03-running-commands' },
              { text: 'File Exchange', link: '/under-the-hood/04-file-exchange' },
              { text: 'Save and Rollback', link: '/under-the-hood/05-save-and-rollback' },
              { text: 'Desktop Control', link: '/under-the-hood/06-desktop-control' },
              { text: 'Network and Isolation', link: '/under-the-hood/07-network-and-isolation' },
              { text: 'Performance', link: '/under-the-hood/08-performance' },
            ],
          },
        ],
        '/contributor-guide/': [
          {
            text: 'Contributor Guide',
            items: [
              { text: 'Overview', link: '/contributor-guide/' },
              { text: 'Creating Images', link: '/contributor-guide/01-creating-images' },
              { text: 'Extending the Sandbox', link: '/contributor-guide/02-extending-the-sandbox' },
              { text: 'Testing', link: '/contributor-guide/03-testing' },
              { text: 'Releasing', link: '/contributor-guide/04-releasing' },
            ],
          },
        ],
        '/packages/': [
          {
            text: 'Runtime',
            items: [
              { text: 'quicksand', link: '/packages/quicksand' },
              { text: 'quicksand-core', link: '/packages/quicksand-core' },
              { text: 'quicksand-qemu', link: '/packages/quicksand-qemu' },
              { text: 'quicksand-smb', link: '/packages/quicksand-smb' },
            ],
          },
          {
            text: 'Images',
            items: [
              { text: 'quicksand-ubuntu', link: '/packages/quicksand-ubuntu' },
              { text: 'quicksand-alpine', link: '/packages/quicksand-alpine' },
              { text: 'quicksand-ubuntu-desktop', link: '/packages/quicksand-ubuntu-desktop' },
              { text: 'quicksand-alpine-desktop', link: '/packages/quicksand-alpine-desktop' },
              { text: 'quicksand-agent', link: '/packages/quicksand-agent' },
              { text: 'quicksand-cua', link: '/packages/quicksand-cua' },
            ],
          },
          {
            text: 'Dev Tools',
            items: [
              { text: 'quicksand-image-tools', link: '/packages/quicksand-image-tools' },
              { text: 'quicksand-base-scaffold', link: '/packages/quicksand-base-scaffold' },
              { text: 'quicksand-overlay-scaffold', link: '/packages/quicksand-overlay-scaffold' },
              { text: 'quicksand-build-tools', link: '/packages/quicksand-build-tools' },
              { text: 'quicksand-gh-runners', link: '/packages/quicksand-gh-runners' },
            ],
          },
        ],
        '/reference/': [
          {
            text: 'Reference',
            items: [
              { text: 'Changelog', link: '/reference/changelog' },
              { text: 'Security', link: '/reference/security' },
            ],
          },
        ],
      },

      socialLinks: [
        { icon: 'github', link: 'https://github.com/microsoft/quicksand' },
      ],

      search: {
        provider: 'local',
      },

      editLink: {
        pattern: 'https://github.com/microsoft/quicksand/edit/main/docs/:path',
      },
    },

    mermaid: {},
  })
)
