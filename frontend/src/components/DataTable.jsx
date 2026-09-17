import { FiChevronLeft, FiChevronRight } from 'react-icons/fi'
import { Empty } from './ui'

/**
 * One sortable, paginated table for both domains.
 *
 * Columns declare how to read and render themselves; the table owns sorting
 * affordances, sticky headers, the empty state and the footer. Stocks and funds
 * differ in their columns, not in how a table behaves, and having one
 * implementation is what keeps them behaving the same.
 */
export default function DataTable({
  columns,
  rows,
  rowKey,
  sort,
  onSort,
  page,
  empty,
}) {
  if (!rows.length) {
    return (
      <Empty
        title={empty?.title || 'No matching rows'}
        hint={empty?.hint || 'Try a different search or filter.'}
      />
    )
  }

  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              {columns.map((column) => {
                const sortable = column.sortable !== false
                const active = sort?.key === column.key
                const classes = [
                  column.align === 'right' ? 'right' : '',
                  sortable ? 'sortable' : '',
                  column.hide ? `hide-${column.hide}` : '',
                ]
                  .filter(Boolean)
                  .join(' ')

                return (
                  <th
                    key={column.key}
                    className={classes}
                    scope="col"
                    aria-sort={active ? (sort.ascending ? 'ascending' : 'descending') : 'none'}
                    onClick={sortable ? () => onSort(column.key) : undefined}
                    title={sortable ? `Sort by ${column.header}` : undefined}
                  >
                    {column.header}
                    <span className="sort-mark" aria-hidden="true">
                      {active ? (sort.ascending ? '▲' : '▼') : ''}
                    </span>
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={rowKey(row, index)}>
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={[
                      column.align === 'right' ? 'right' : '',
                      column.hide ? `hide-${column.hide}` : '',
                    ]
                      .filter(Boolean)
                      .join(' ')}
                  >
                    {column.render(row, index)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {page && (
        <div className="table-foot">
          <span>
            Showing <b className="num">{page.from.toLocaleString()}</b>–
            <b className="num">{page.to.toLocaleString()}</b> of{' '}
            <b className="num">{page.total.toLocaleString()}</b>
          </span>
          <span className="spacer" />
          <button
            type="button"
            className="btn btn--sm"
            onClick={page.previous}
            disabled={page.page === 0 || page.pages === 1}
          >
            <FiChevronLeft aria-hidden="true" />
            Previous
          </button>
          <span className="num">
            {page.page + 1} / {page.pages}
          </span>
          <button
            type="button"
            className="btn btn--sm"
            onClick={page.next}
            disabled={page.page >= page.pages - 1}
          >
            Next
            <FiChevronRight aria-hidden="true" />
          </button>
        </div>
      )}
    </>
  )
}
