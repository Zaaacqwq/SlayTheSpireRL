// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { I18nProvider, useI18n } from './i18n'
import { InfoTip } from './components/ui'

function Probe() {
  const { t, locale, setLocale } = useI18n()
  return <><span>{t('nav.overview')}</span><button onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}>toggle</button></>
}

describe('internationalization and metric help', () => {
  beforeEach(() => localStorage.clear())

  it('switches languages and persists the choice', () => {
    render(<I18nProvider><Probe /></I18nProvider>)
    expect(screen.getByText('训练总览')).toBeInTheDocument()
    fireEvent.click(screen.getByText('toggle'))
    expect(screen.getByText('Overview')).toBeInTheDocument()
    expect(localStorage.getItem('sts2-rl-locale')).toBe('en-US')
  })

  it('exposes tooltip text to assistive technology', () => {
    render(<InfoTip text="What this metric means" />)
    expect(screen.getByRole('tooltip')).toHaveTextContent('What this metric means')
    expect(screen.getByRole('button', { name: 'Info' })).toBeInTheDocument()
  })
})
