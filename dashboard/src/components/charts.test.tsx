// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test } from 'vitest'
import { MetricChart } from './charts'
import { I18nProvider } from '../i18n'
import type { MetricRow } from '../types'

afterEach(() => cleanup())

const ROWS: MetricRow[] = [
  { iteration: 0, a: 0.1, b: 0.2 },
  { iteration: 1, a: 0.3, b: 0.4 },
]
const SERIES = [
  { key: 'a', name: 'Alpha', color: 'red' },
  { key: 'b', name: 'Beta', color: 'blue' },
]

const view = (props: Partial<Parameters<typeof MetricChart>[0]> = {}) =>
  render(
    <I18nProvider>
      <MetricChart rows={ROWS} segments={[]} height={100} series={SERIES} {...props} />
    </I18nProvider>,
  )

const legend = () => screen.getByText('Alpha').closest('.legend-row') as HTMLElement

test('every multi-series chart gets a clickable legend filter by default', () => {
  view()
  const buttons = within(legend()).getAllByRole('button')
  expect(buttons).toHaveLength(2)
  expect(buttons.every(b => b.getAttribute('aria-pressed') === 'true')).toBe(true)
})

test('clicking a legend entry hides its line and toggles it back', () => {
  view()
  const beta = screen.getByRole('button', { name: 'Beta' })
  fireEvent.click(beta)
  expect(beta).toHaveAttribute('aria-pressed', 'false')
  expect(beta.className).toContain('off')
  fireEvent.click(beta)
  expect(beta).toHaveAttribute('aria-pressed', 'true')
  expect(beta.className).not.toContain('off')
})

test('defaultHidden seeds which series start hidden, still one click away', () => {
  view({ defaultHidden: ['b'] })
  expect(screen.getByRole('button', { name: 'Alpha' })).toHaveAttribute('aria-pressed', 'true')
  const beta = screen.getByRole('button', { name: 'Beta' })
  expect(beta).toHaveAttribute('aria-pressed', 'false')
  fireEvent.click(beta)
  expect(beta).toHaveAttribute('aria-pressed', 'true')
})

test('filterable={false} keeps a static, non-interactive legend', () => {
  view({ filterable: false })
  expect(within(legend()).queryByRole('button')).toBeNull()
  expect(screen.getByText('Alpha')).toBeInTheDocument()
})
