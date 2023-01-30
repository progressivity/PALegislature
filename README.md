# PALegislature
Database of votes from PA Legislative chambers

The data is gathered from [The Official Pennsylvania Legsilature pages](https://www.legis.state.pa.us/), as well as the [House](https://archives.house.state.pa.us/people/house-historical-biographies) and [Senate Historical Biographies](https://www.legis.state.pa.us/cfdocs/legis/BiosHistory/ViewAll.cfm?body=S).

The goal is to provide an easily-parsed record of how legislators voted.

## The Data
The voting data itself is contained within the [`vote_data`](vote_data) folder, organized by year and chamber (House/Senate).

Each `csv` file has the same structure, with three special rows and three special columns.

| Name       | Number | Date                | Legislator1 Name       | Legislator2 Name       |
|------------|--------|---------------------|------------------------|------------------------|
| District   |        |                     | Legislator1 District   | Legislator2 District   |
| Party      |        |                     | Legislator1 Party      | Legislator2 Party      |
| Roll1 Name | 1      | YYYY-MM-DD HH:MM:SS | Legislator1 Roll1 Vote | Legislator2 Roll1 Vote |
| Roll2 Name | 2      | YYYY-MM-DD HH:MM:SS | Legislator1 Roll2 Vote | Legislator2 Roll2 Vote |

 * The first three rows have information about the *Legislator* (name, district number and party affiliation)
 * The first three columns have information about the *Roll Call* (name, number, and the date/time).
 * There are **five** possible values for the vote:
   * **Y** - Yea
   * **N** - Nay
   * **X** - No Vote
   * **E** - Leave
   * [blank] - No record (i.e. before or after legislator was active)
 * If the timestamp is not found on the roll page, the time is omitted leaving just the date.

## The Crawling Process

To be documented...
