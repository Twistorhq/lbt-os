import { render, screen, fireEvent } from '@testing-library/react'
import DispatchMap from './DispatchMap'
import DemoKpiStrip from './DemoKpiStrip'

test('KPI strip reads the demo data layer as given', () => {
  render(<DemoKpiStrip />)
  expect(screen.getByText(/Front Range Comfort Co\. · Monday/)).toBeInTheDocument()
  expect(screen.getByText('34')).toBeInTheDocument()
  expect(screen.getByText('$50,839')).toBeInTheDocument()
  expect(screen.getByText('$8,081')).toBeInTheDocument()
  expect(screen.getByText(/7 done · 9 on site · 5 en route · 13 scheduled/)).toBeInTheDocument()
})

test('map renders all 34 job pins and the verbatim labeling line', () => {
  const { container } = render(<DispatchMap />)
  expect(container.querySelectorAll('svg title')).toHaveLength(34)
  expect(
    screen.getByText(
      'Demo data: Front Range Comfort Co. is a fictional shop. Pins, routes, coverage, and the storm cell are illustrative and are not live customer data.',
    ),
  ).toBeInTheDocument()
  expect(screen.getByRole('img', { name: /Demo dispatch map of the Denver metro/ })).toBeInTheDocument()
})

test('board lists the four crews with stops, route miles, and revenue', () => {
  render(<DispatchMap />)
  expect(screen.getAllByText(/Crew North/).length).toBeGreaterThanOrEqual(1)
  expect(screen.getByText(/9 stops · 76\.8 route miles · \$16,243/)).toBeInTheDocument()
  expect(screen.getByText(/5 jobs recovered today · \$2,853/)).toBeInTheDocument()
})

test('storm callout names the cell, the window, and the five at-risk jobs', () => {
  render(<DispatchMap />)
  expect(screen.getByText('Storm Cell DEMO-1 (fictional)')).toBeInTheDocument()
  expect(screen.getByText(/hail to 1 inch/)).toBeInTheDocument()
  expect(screen.getByText(/\$8,081 across 5 jobs in the path/)).toBeInTheDocument()
  expect(screen.getByText(/1:00 PM · AC system install/)).toBeInTheDocument()
  expect(screen.getByText(/3:15 PM · Heat pump repair/)).toBeInTheDocument()
})

test('storm layer toggle hides the storm callout and reports pressed state', () => {
  render(<DispatchMap />)
  const toggle = screen.getByRole('button', { name: 'Storm watch' })
  expect(toggle).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(screen.queryByText('Storm Cell DEMO-1 (fictional)')).not.toBeInTheDocument()
  fireEvent.click(toggle)
  expect(screen.getByText('Storm Cell DEMO-1 (fictional)')).toBeInTheDocument()
})
